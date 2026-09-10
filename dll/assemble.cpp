// SPDX-License-Identifier: MIT
// Minimal Linux client for Microsoft's documented IDxcAssembler interface.
// Build-only tool: no Vulkan, Wine, or GPU execution.
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <string>
#include <dlfcn.h>
struct Guid { uint32_t a; uint16_t b,c; uint8_t d[8]; };
using HR=int32_t;
struct Unknown {
    virtual HR QueryInterface(const Guid&,void**)=0;
    virtual uint32_t AddRef()=0;
    virtual uint32_t Release()=0;
};
struct Blob : Unknown { virtual void* GetBufferPointer()=0; virtual size_t GetBufferSize()=0; };
struct Encoding : Blob { virtual HR GetEncoding(int*,uint32_t*)=0; };
struct Result : Unknown {
    virtual HR GetStatus(HR*)=0;
    virtual HR GetResult(Blob**)=0;
    virtual HR GetErrorBuffer(Encoding**)=0;
};
struct Assembler : Unknown { virtual HR AssembleToContainer(Blob*,Result**)=0; };
struct Validator : Unknown { virtual HR Validate(Blob*,uint32_t,Result**)=0; };
struct Input final : Blob {
    std::string bytes;
    explicit Input(const char* path) { std::ifstream f(path,std::ios::binary); bytes.assign(std::istreambuf_iterator<char>(f),{}); }
    HR QueryInterface(const Guid&,void** p) override {*p=nullptr; return HR(0x80004002);}
    uint32_t AddRef() override {return 1;}
    uint32_t Release() override {return 1;}
    void* GetBufferPointer() override {return bytes.data();}
    size_t GetBufferSize() override {return bytes.size();}
};
static Blob* output(Result* result) {
    if (!result) return nullptr;
    Encoding* errors=nullptr; result->GetErrorBuffer(&errors);
    if (errors) {std::fwrite(errors->GetBufferPointer(),1,errors->GetBufferSize(),stderr); errors->Release();}
    HR status=-1; result->GetStatus(&status);
    if (status<0) {std::fprintf(stderr,"DXC status 0x%x\n",unsigned(status)); return nullptr;}
    Blob* blob=nullptr; result->GetResult(&blob); return blob;
}
int main(int argc,char** argv) {
    if (argc!=5 || (std::string(argv[2])!="assemble" && std::string(argv[2])!="validate")) return 2;
    void* library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);
    if (!library) {std::fprintf(stderr,"%s\n",dlerror());return 2;}
    using Create=HR(*)(const Guid&,const Guid&,void**);
    auto create=reinterpret_cast<Create>(dlsym(library,"DxcCreateInstance"));
    if (!create) return 2;
    Input input(argv[3]); if(input.bytes.empty()) return 2;
    const Guid ac={0xd728db68,0xf903,0x4f80,{0x94,0xcd,0xdc,0xcf,0x76,0xec,0x71,0x51}};
    const Guid ai={0x091f7a26,0x1c1f,0x4948,{0x90,0x4b,0xe6,0xe3,0xa8,0xa7,0x71,0xd5}};
    const Guid vc={0x8ca3e215,0xf728,0x4cf3,{0x8c,0xdd,0x88,0xaf,0x91,0x75,0x87,0xa1}};
    const Guid vi={0xa6e82bd2,0x1fd7,0x4826,{0x98,0x11,0x28,0x57,0xe7,0x97,0xf4,0x9a}};
    Result* result=nullptr; Unknown* owner=nullptr; HR hr;
    if(std::string(argv[2])=="assemble") {
        Assembler* a=nullptr; hr=create(ac,ai,reinterpret_cast<void**>(&a));
        if(hr<0 || !a)return 3;
        owner=a;hr=a->AssembleToContainer(&input,&result);
    } else {
        Validator* v=nullptr;hr=create(vc,vi,reinterpret_cast<void**>(&v));
        if(hr<0 || !v)return 3;
        owner=v;hr=v->Validate(&input,0,&result);
    }
    if(hr<0){std::fprintf(stderr,"call failed 0x%x\n",unsigned(hr));return 4;}
    Blob* blob=output(result);if(!blob)return 5;
    std::ofstream f(argv[4],std::ios::binary);
    f.write(static_cast<const char*>(blob->GetBufferPointer()),blob->GetBufferSize());
    bool good=bool(f);f.close();blob->Release();result->Release();owner->Release();
    return good?0:6;
}
