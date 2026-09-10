/* SPDX-License-Identifier: MIT
 * Headless D3D12/FFX probe. Optional pass tracing stays inside this process.
 * Select fresh scratch output paths; no game files or save roots are needed. */
#define COBJMACROS
#define INITGUID
#include <windows.h>
#include <d3d12.h>
#include <tlhelp32.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <ffx_upscale.h>
static const GUID device_iid={0x189819f1,0x1db6,0x4b57,{0xbe,0x54,0x18,0x21,0x33,0x9b,0x85,0xf7}};
struct Backend {ffxCreateContextDescHeader header; ID3D12Device* device;};
static HANDLE output;
static UINT render_w=128,render_h=96,output_w=192,output_h=144,frame_count=4;
static UINT maximum_w=192,maximum_h=144;
static char scenario[32]="static";
static void log_line(const char* text);
static bool trace_passes=false;
static ID3D12QueryHeap* pass_queries=NULL;
static UINT pass_count=0,pass_frame=0;
#ifdef BC250_PROBE_BARRIER_AUDIT
#include "barrier_audit.h"
#else
#define audit_barriers_before_dispatch(frame,index) ((void)0)
#define install_barrier_audit(list) ((void)0)
#define remove_barrier_audit(list) ((void)0)
#endif
static void (STDMETHODCALLTYPE *original_dispatch)(ID3D12GraphicsCommandList*,UINT,UINT,UINT)=NULL;
static void STDMETHODCALLTYPE traced_dispatch(ID3D12GraphicsCommandList* list,UINT x,UINT y,UINT z) {
    if(pass_count>=128)ExitProcess(22);
    UINT index=pass_count++;
    audit_barriers_before_dispatch(pass_frame,index);
    if(pass_frame==0){char line[160];snprintf(line,sizeof(line),"pass_shape: index=%u groups=%u,%u,%u\n",index,x,y,z);log_line(line);}
    ID3D12GraphicsCommandList_EndQuery(list,pass_queries,D3D12_QUERY_TYPE_TIMESTAMP,index*2);
    original_dispatch(list,x,y,z);
    ID3D12GraphicsCommandList_EndQuery(list,pass_queries,D3D12_QUERY_TYPE_TIMESTAMP,index*2+1);
}
static void replace_pointer(void* slot,void* pointer) {
    DWORD old=0,unused=0;
    if(!VirtualProtect(slot,sizeof(pointer),PAGE_READWRITE,&old))ExitProcess(22);
    memcpy(slot,&pointer,sizeof(pointer));
    if(!VirtualProtect(slot,sizeof(pointer),old,&unused))ExitProcess(22);
}
static UINT get_uint(const char* key,UINT fallback,UINT minimum,UINT maximum) {
    char value[32];DWORD n=GetEnvironmentVariableA(key,value,sizeof(value));
    if(!n)return fallback;
    if(n>=sizeof(value))ExitProcess(20);
    char* end=NULL;unsigned long parsed=strtoul(value,&end,10);
    if(!end||*end||parsed<minimum||parsed>maximum)ExitProcess(20);
    return (UINT)parsed;
}
int _fltused;
static void log_line(const char* text) {
    /* Per-pass fsync starves the queue and perturbs the dynamic GPU clock. */
    DWORD n=0; WriteFile(output,text,(DWORD)strlen(text),&n,NULL);
}
static void message(uint32_t kind,const wchar_t* text) {
    char line[1900]; char msg[1500];
    WideCharToMultiByte(CP_UTF8,0,text,-1,msg,sizeof(msg),NULL,NULL);
    snprintf(line,sizeof(line),"FFX message %u: %s\n",kind,msg);log_line(line);
}
static void check(HRESULT hr,const char* call) {
    if(FAILED(hr)) {char s[512];snprintf(s,sizeof(s),"FAILED %s: %08lx\n",call,hr);log_line(s);ExitProcess(10);}
}
#define HR(call) check((call),#call)
struct Texture {ID3D12Resource* resource;ID3D12Resource* upload;D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint;UINT64 bytes;UINT w,h;UINT ffx_format;};
static ID3D12Resource* buffer(ID3D12Device* dev,UINT64 size,D3D12_HEAP_TYPE heap) {
    D3D12_HEAP_PROPERTIES hp={.Type=heap,.CreationNodeMask=1,.VisibleNodeMask=1};
    D3D12_RESOURCE_DESC rd={.Dimension=D3D12_RESOURCE_DIMENSION_BUFFER,.Width=size,.Height=1,
       .DepthOrArraySize=1,.MipLevels=1,.SampleDesc={1,0},.Layout=D3D12_TEXTURE_LAYOUT_ROW_MAJOR};
    ID3D12Resource* r=NULL;
    HR(ID3D12Device_CreateCommittedResource(dev,&hp,D3D12_HEAP_FLAG_NONE,&rd,
       heap==D3D12_HEAP_TYPE_UPLOAD?D3D12_RESOURCE_STATE_GENERIC_READ:D3D12_RESOURCE_STATE_COPY_DEST,
       NULL,&IID_ID3D12Resource,(void**)&r));return r;
}
static void barrier(ID3D12GraphicsCommandList* list,ID3D12Resource* resource,D3D12_RESOURCE_STATES before,D3D12_RESOURCE_STATES after) {
    D3D12_RESOURCE_BARRIER b={.Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
       .Transition={.pResource=resource,.Subresource=D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES,.StateBefore=before,.StateAfter=after}};
    ID3D12GraphicsCommandList_ResourceBarrier(list,1,&b);
}
static struct Texture texture(ID3D12Device* dev,ID3D12GraphicsCommandList* list,UINT w,UINT h,UINT channels,int kind) {
    DXGI_FORMAT fmt=channels==4?DXGI_FORMAT_R32G32B32A32_FLOAT:channels==2?DXGI_FORMAT_R32G32_FLOAT:DXGI_FORMAT_R32_FLOAT;
    struct Texture t={.w=w,.h=h,.ffx_format=channels==4?FFX_API_SURFACE_FORMAT_R32G32B32A32_FLOAT:channels==2?FFX_API_SURFACE_FORMAT_R32G32_FLOAT:FFX_API_SURFACE_FORMAT_R32_FLOAT};
    D3D12_HEAP_PROPERTIES hp={.Type=D3D12_HEAP_TYPE_DEFAULT,.CreationNodeMask=1,.VisibleNodeMask=1};
    D3D12_RESOURCE_DESC rd={.Dimension=D3D12_RESOURCE_DIMENSION_TEXTURE2D,.Width=w,.Height=h,
       .DepthOrArraySize=1,.MipLevels=1,.Format=fmt,.SampleDesc={1,0},.Layout=D3D12_TEXTURE_LAYOUT_UNKNOWN,
       .Flags=kind==3?D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS:D3D12_RESOURCE_FLAG_NONE};
    HR(ID3D12Device_CreateCommittedResource(dev,&hp,D3D12_HEAP_FLAG_NONE,&rd,
        kind==3?D3D12_RESOURCE_STATE_UNORDERED_ACCESS:D3D12_RESOURCE_STATE_COPY_DEST,NULL,&IID_ID3D12Resource,(void**)&t.resource));
    ID3D12Device_GetCopyableFootprints(dev,&rd,0,1,0,&t.footprint,NULL,NULL,&t.bytes);
    if(kind!=3) {
        t.upload=buffer(dev,t.bytes,D3D12_HEAP_TYPE_UPLOAD);void* mapped=NULL;D3D12_RANGE no_read={0,0};
        HR(ID3D12Resource_Map(t.upload,0,&no_read,&mapped));memset(mapped,0,(size_t)t.bytes);
        for(UINT y=0;y<h;y++)for(UINT x=0;x<w;x++) {
            float* px=(float*)((char*)mapped+t.footprint.Offset+y*t.footprint.Footprint.RowPitch)+x*channels;
            if(kind==0){px[0]=.1f+.6f*(float)x/(float)w;px[1]=.2f+.3f*(float)y/(float)h;px[2]=((x/8+y/8)&1)?.7f:.1f;px[3]=1.f;
                if(!strcmp(scenario,"hdr")){px[0]*=16.f;px[1]*=4.f;px[2]*=8.f;}}
            else if(kind==1)px[0]=.5f+.1f*(float)y/(float)h;
            else if(kind==2&&!strcmp(scenario,"motion")){px[0]=.002f;px[1]=-.001f;}
        }
        ID3D12Resource_Unmap(t.upload,0,NULL);
        D3D12_TEXTURE_COPY_LOCATION src={.pResource=t.upload,.Type=D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT,.PlacedFootprint=t.footprint};
        D3D12_TEXTURE_COPY_LOCATION dst={.pResource=t.resource,.Type=D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX,.SubresourceIndex=0};
        ID3D12GraphicsCommandList_CopyTextureRegion(list,&dst,0,0,0,&src,NULL);
        barrier(list,t.resource,D3D12_RESOURCE_STATE_COPY_DEST,D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    }
    return t;
}
static struct FfxApiResource api_resource(struct Texture t,bool writable) {
    return (struct FfxApiResource){.resource=t.resource,.description={.type=FFX_API_RESOURCE_TYPE_TEXTURE2D,
      .format=t.ffx_format,.width=t.w,.height=t.h,.depth=1,.mipCount=1,.usage=writable?FFX_API_RESOURCE_USAGE_UAV:FFX_API_RESOURCE_USAGE_READ_ONLY},
      .state=writable?FFX_API_RESOURCE_STATE_UNORDERED_ACCESS:FFX_API_RESOURCE_STATE_COMPUTE_READ};
}
static void render(ID3D12Device* dev,ffxContext* context,PfnFfxDispatch dispatch) {
    ID3D12CommandQueue* queue=NULL;ID3D12CommandAllocator* allocator=NULL;ID3D12GraphicsCommandList* list=NULL;ID3D12Fence* fence=NULL;
    D3D12_COMMAND_QUEUE_DESC qd={.Type=D3D12_COMMAND_LIST_TYPE_DIRECT};
    HR(ID3D12Device_CreateCommandQueue(dev,&qd,&IID_ID3D12CommandQueue,(void**)&queue));
    HR(ID3D12Device_CreateCommandAllocator(dev,D3D12_COMMAND_LIST_TYPE_DIRECT,&IID_ID3D12CommandAllocator,(void**)&allocator));
    HR(ID3D12Device_CreateCommandList(dev,0,D3D12_COMMAND_LIST_TYPE_DIRECT,allocator,NULL,&IID_ID3D12GraphicsCommandList,(void**)&list));
    HR(ID3D12Device_CreateFence(dev,0,D3D12_FENCE_FLAG_NONE,&IID_ID3D12Fence,(void**)&fence));
    HANDLE event=CreateEventW(NULL,FALSE,FALSE,NULL);if(!event)ExitProcess(11);
    struct Texture t[4]={texture(dev,list,render_w,render_h,4,0),texture(dev,list,render_w,render_h,1,1),texture(dev,list,render_w,render_h,2,2),texture(dev,list,output_w,output_h,4,3)};
    ID3D12Resource* readback=buffer(dev,t[3].bytes,D3D12_HEAP_TYPE_READBACK);
    ID3D12QueryHeap* queries=NULL;D3D12_QUERY_HEAP_DESC query_desc={.Type=D3D12_QUERY_HEAP_TYPE_TIMESTAMP,.Count=2};
    HR(ID3D12Device_CreateQueryHeap(dev,&query_desc,&IID_ID3D12QueryHeap,(void**)&queries));
    ID3D12Resource* timestamps=buffer(dev,16,D3D12_HEAP_TYPE_READBACK);UINT64 frequency=0;
    HR(ID3D12CommandQueue_GetTimestampFrequency(queue,&frequency));if(!frequency)ExitProcess(21);
    ID3D12Resource* pass_times=NULL;
    if(trace_passes){
        install_barrier_audit(list);
        D3D12_QUERY_HEAP_DESC desc={.Type=D3D12_QUERY_HEAP_TYPE_TIMESTAMP,.Count=256};
        HR(ID3D12Device_CreateQueryHeap(dev,&desc,&IID_ID3D12QueryHeap,(void**)&pass_queries));
        pass_times=buffer(dev,2048,D3D12_HEAP_TYPE_READBACK);
        original_dispatch=list->lpVtbl->Dispatch;
        replace_pointer((void*)&list->lpVtbl->Dispatch,(void*)traced_dispatch);
    }
    for(UINT frame=0;frame<frame_count;frame++) {
        if(frame){HR(ID3D12CommandAllocator_Reset(allocator));HR(ID3D12GraphicsCommandList_Reset(list,allocator,NULL));}
        UINT rw=render_w,rh=render_h;
        if(!strcmp(scenario,"resize")&&frame%4>=2){rw=render_w*3/4;rh=render_h*3/4;}
        struct ffxDispatchDescUpscale d={.header={FFX_API_DISPATCH_DESC_TYPE_UPSCALE,NULL},.commandList=list,
            .color=api_resource(t[0],false),.depth=api_resource(t[1],false),.motionVectors=api_resource(t[2],false),
            .output=api_resource(t[3],true),.jitterOffset={0,0},.motionVectorScale={(float)rw,(float)rh},.renderSize={rw,rh},.upscaleSize={output_w,output_h},
            .frameTimeDelta=16.666667f,.preExposure=1.f,.reset=frame==0||(!strcmp(scenario,"reset")&&frame%4==0),.cameraNear=.1f,.cameraFar=100.f,
            .cameraFovAngleVertical=1.04719755f,.viewSpaceToMetersFactor=1.f,.flags=0};
        if(!strcmp(scenario,"motion")){d.jitterOffset.x=(frame%4)*.25f-.375f;d.jitterOffset.y=(frame%3)*.3333333f-.3333333f;}
        if(!strcmp(scenario,"rcas")){d.enableSharpening=true;d.sharpness=.4f;}
        ID3D12GraphicsCommandList_EndQuery(list,queries,D3D12_QUERY_TYPE_TIMESTAMP,0);
        pass_count=0;pass_frame=frame;
        ffxReturnCode_t rc=dispatch(context,&d.header);char line[256];
        snprintf(line,sizeof(line),"dispatch: frame=%u status=%u\n",frame,rc);log_line(line);if(rc)ExitProcess(12);
        ID3D12GraphicsCommandList_EndQuery(list,queries,D3D12_QUERY_TYPE_TIMESTAMP,1);
        ID3D12GraphicsCommandList_ResolveQueryData(list,queries,D3D12_QUERY_TYPE_TIMESTAMP,0,2,timestamps,0);
        if(trace_passes&&pass_count)ID3D12GraphicsCommandList_ResolveQueryData(list,pass_queries,D3D12_QUERY_TYPE_TIMESTAMP,0,pass_count*2,pass_times,0);
        barrier(list,t[3].resource,D3D12_RESOURCE_STATE_UNORDERED_ACCESS,D3D12_RESOURCE_STATE_COPY_SOURCE);
        D3D12_TEXTURE_COPY_LOCATION src={.pResource=t[3].resource,.Type=D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX,.SubresourceIndex=0};
        D3D12_TEXTURE_COPY_LOCATION dst={.pResource=readback,.Type=D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT,.PlacedFootprint=t[3].footprint};
        ID3D12GraphicsCommandList_CopyTextureRegion(list,&dst,0,0,0,&src,NULL);
        barrier(list,t[3].resource,D3D12_RESOURCE_STATE_COPY_SOURCE,D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        HR(ID3D12GraphicsCommandList_Close(list));ID3D12CommandList* lists[]={(ID3D12CommandList*)list};
        ID3D12CommandQueue_ExecuteCommandLists(queue,1,lists);HR(ID3D12CommandQueue_Signal(queue,fence,frame+1));
        HR(ID3D12Fence_SetEventOnCompletion(fence,frame+1,event));
        DWORD wait=WaitForSingleObject(event,30000);if(wait!=WAIT_OBJECT_0){log_line("FENCE TIMEOUT: STOP\n");ExitProcess(13);}
        snprintf(line,sizeof(line),"completed: frame=%u fence=%llu\n",frame,(unsigned long long)ID3D12Fence_GetCompletedValue(fence));log_line(line);
        void* times=NULL;D3D12_RANGE time_read={0,16};HR(ID3D12Resource_Map(timestamps,0,&time_read,&times));
        UINT64 begin=((UINT64*)times)[0],end=((UINT64*)times)[1];
        D3D12_RANGE time_no_write={0,0};ID3D12Resource_Unmap(timestamps,0,&time_no_write);
        if(end<begin)ExitProcess(21);
        snprintf(line,sizeof(line),"gpu_ms: frame=%u value=%.9f\n",frame,(double)(end-begin)*1000.0/(double)frequency);log_line(line);
        if(trace_passes&&pass_count){
            void* mapped=NULL;D3D12_RANGE range={0,pass_count*16};
            HR(ID3D12Resource_Map(pass_times,0,&range,&mapped));
            for(UINT i=0;i<pass_count;i++){
                UINT64 begin=((UINT64*)mapped)[i*2],end=((UINT64*)mapped)[i*2+1];if(end<begin)ExitProcess(22);
                snprintf(line,sizeof(line),"pass_ms: frame=%u index=%u value=%.9f\n",frame,i,(double)(end-begin)*1000.0/(double)frequency);log_line(line);
            }
            ID3D12Resource_Unmap(pass_times,0,&time_no_write);
        }
    }
    static wchar_t path[4096];DWORD n=GetEnvironmentVariableW(L"BC250_FFX_PIXELS",path,4096);if(!n||n>=4096)ExitProcess(14);
    HANDLE file=CreateFileW(path,GENERIC_WRITE,0,NULL,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,NULL);if(file==INVALID_HANDLE_VALUE)ExitProcess(14);
    void* mapped=NULL;D3D12_RANGE read={0,(SIZE_T)t[3].bytes};HR(ID3D12Resource_Map(readback,0,&read,&mapped));
    for(UINT y=0;y<t[3].h;y++) {DWORD done=0;if(!WriteFile(file,(char*)mapped+t[3].footprint.Offset+y*t[3].footprint.Footprint.RowPitch,t[3].w*16,&done,NULL)||done!=t[3].w*16)ExitProcess(14);}
    D3D12_RANGE no_write={0,0};ID3D12Resource_Unmap(readback,0,&no_write);CloseHandle(file);
    ID3D12Resource_Release(readback);
    ID3D12Resource_Release(timestamps);ID3D12QueryHeap_Release(queries);
    if(trace_passes){remove_barrier_audit(list);replace_pointer((void*)&list->lpVtbl->Dispatch,(void*)original_dispatch);ID3D12Resource_Release(pass_times);ID3D12QueryHeap_Release(pass_queries);}
    for(UINT k=0;k<4;k++){if(t[k].upload)ID3D12Resource_Release(t[k].upload);ID3D12Resource_Release(t[k].resource);}
    CloseHandle(event);ID3D12Fence_Release(fence);ID3D12GraphicsCommandList_Release(list);ID3D12CommandAllocator_Release(allocator);ID3D12CommandQueue_Release(queue);
    char line[200];snprintf(line,sizeof(line),"readback: %ux%u RGBA32F, %u completed frames\n",output_w,output_h,frame_count);log_line(line);
}
void mainCRTStartup(void) {
    static wchar_t path[4096]; static char line[4096]; DWORD n;
    n=GetEnvironmentVariableW(L"BC250_FFX_OUTPUT",path,4096);
    if(!n || n>=4096)ExitProcess(2);
    output=CreateFileW(path,GENERIC_WRITE,FILE_SHARE_READ,NULL,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
    if(output==INVALID_HANDLE_VALUE)ExitProcess(2);
    render_w=get_uint("BC250_FFX_RENDER_W",128,16,3840);render_h=get_uint("BC250_FFX_RENDER_H",96,16,2160);
    output_w=get_uint("BC250_FFX_OUTPUT_W",192,16,3840);output_h=get_uint("BC250_FFX_OUTPUT_H",144,16,2160);
    maximum_w=get_uint("BC250_FFX_MAX_OUTPUT_W",output_w,16,7680);maximum_h=get_uint("BC250_FFX_MAX_OUTPUT_H",output_h,16,4320);
    frame_count=get_uint("BC250_FFX_FRAMES",4,1,600);
    trace_passes=get_uint("BC250_FFX_TRACE",0,0,1)!=0;
    n=GetEnvironmentVariableA("BC250_FFX_SCENARIO",scenario,sizeof(scenario));
    if(n>=sizeof(scenario))ExitProcess(20);
    if(!n)strcpy(scenario,"static");
    if(strcmp(scenario,"static")&&strcmp(scenario,"sdr")&&strcmp(scenario,"hdr")&&strcmp(scenario,"motion")&&strcmp(scenario,"reset")&&strcmp(scenario,"resize")&&strcmp(scenario,"rcas"))ExitProcess(20);
    if(output_w<render_w||output_h<render_h)ExitProcess(20);
    if(maximum_w<output_w||maximum_h<output_h)ExitProcess(20);
    log_line("probe_revision: 3 (no per-line flush; explicit maximum size)\n");
    snprintf(line,sizeof(line),"maximum_size: %ux%u\n",maximum_w,maximum_h);log_line(line);
    snprintf(line,sizeof(line),"workload: render=%ux%u output=%ux%u frames=%u scenario=%s\n",render_w,render_h,output_w,output_h,frame_count,scenario);log_line(line);
    n=GetEnvironmentVariableW(L"BC250_FFX_DLL",path,4096);
    if(!n || n>=4096)ExitProcess(2);
    HMODULE dll=LoadLibraryW(path);
    if(!dll){snprintf(line,sizeof(line),"load failed: %lu\n",GetLastError());log_line(line);ExitProcess(3);}
    PfnFfxQuery query=(PfnFfxQuery)GetProcAddress(dll,"ffxQuery");
    PfnFfxCreateContext create=(PfnFfxCreateContext)GetProcAddress(dll,"ffxCreateContext");
    PfnFfxDestroyContext destroy=(PfnFfxDestroyContext)GetProcAddress(dll,"ffxDestroyContext");
    if(!query || !create || !destroy)ExitProcess(4);
    HMODULE dx=LoadLibraryW(L"d3d12.dll");
    HRESULT (WINAPI *new_device)(IUnknown*,D3D_FEATURE_LEVEL,REFIID,void**)=
       (void*)GetProcAddress(dx,"D3D12CreateDevice");
    if(!new_device)ExitProcess(5);
    ID3D12Device* device=NULL;HRESULT hr=new_device(NULL,D3D_FEATURE_LEVEL_12_0,&device_iid,(void**)&device);
    snprintf(line,sizeof(line),"D3D12CreateDevice: 0x%08lx\n",hr);log_line(line);
    if(FAILED(hr))ExitProcess(6);
    uint64_t count=16, ids[16]={0}; const char* names[16]={0};
    struct ffxQueryDescGetVersions versions={.header={FFX_API_QUERY_DESC_TYPE_GET_VERSIONS,NULL},
       .createDescType=FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE,.device=device,
       .outputCount=&count,.versionIds=ids,.versionNames=names};
    ffxReturnCode_t rc=query(NULL,&versions.header);
    snprintf(line,sizeof(line),"versions: status=%u count=%llu\n",rc,(unsigned long long)count);log_line(line);
    if(rc || count>16)ExitProcess(7);
    for(unsigned i=0;i<count;i++){
      snprintf(line,sizeof(line),"version: id=%llu name=%s\n",(unsigned long long)ids[i],names[i]?names[i]:"null");log_line(line);
    }
    struct ffxCreateContextDescUpscaleVersion api_version={.header={FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE_VERSION,NULL},.version=FFX_UPSCALER_VERSION};
    struct Backend backend={.header={2,&api_version.header},.device=device};
    struct ffxCreateContextDescUpscale desc={.header={FFX_API_CREATE_CONTEXT_DESC_TYPE_UPSCALE,&backend.header},
       .flags=FFX_UPSCALE_ENABLE_HIGH_DYNAMIC_RANGE|FFX_UPSCALE_ENABLE_AUTO_EXPOSURE,
       .maxRenderSize={render_w,render_h},.maxUpscaleSize={maximum_w,maximum_h},.fpMessage=message};
    if(!strcmp(scenario,"sdr"))desc.flags&=~FFX_UPSCALE_ENABLE_HIGH_DYNAMIC_RANGE;
    if(!strcmp(scenario,"resize"))desc.flags|=FFX_UPSCALE_ENABLE_DYNAMIC_RESOLUTION;
    ffxContext context=NULL;rc=create(&context,&desc.header,NULL);
    snprintf(line,sizeof(line),"create: status=%u context=%p\n",rc,context);log_line(line);
    if(rc || !context)ExitProcess(8);
    struct ffxQueryGetProviderVersion version={.header={FFX_API_QUERY_DESC_TYPE_GET_PROVIDER_VERSION,NULL}};
    rc=query(&context,&version.header);
    snprintf(line,sizeof(line),"provider: status=%u id=%llu name=%s\n",rc,(unsigned long long)version.versionId,version.versionName?version.versionName:"null");log_line(line);
    PfnFfxDispatch dispatch=(PfnFfxDispatch)GetProcAddress(dll,"ffxDispatch");if(!dispatch)ExitProcess(4);
    render(device,&context,dispatch);
    HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,GetCurrentProcessId());
    MODULEENTRY32W module={.dwSize=sizeof(module)};
    if(snapshot!=INVALID_HANDLE_VALUE && Module32FirstW(snapshot,&module))do {
        char module_path[1300];WideCharToMultiByte(CP_UTF8,0,module.szExePath,-1,module_path,sizeof(module_path),NULL,NULL);
        snprintf(line,sizeof(line),"module: %s\n",module_path);log_line(line);
    }while(Module32NextW(snapshot,&module));
    if(snapshot!=INVALID_HANDLE_VALUE)CloseHandle(snapshot);
    rc=destroy(&context,NULL);snprintf(line,sizeof(line),"destroy: status=%u\n",rc);log_line(line);
    ID3D12Device_Release(device);FreeLibrary(dll);Sleep(1000);ExitProcess(rc?9:0);
}
