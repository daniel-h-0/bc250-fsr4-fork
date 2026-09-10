/* SPDX-License-Identifier: MIT
 * Optional, observation-only audit for the standalone probe. This records
 * the SDK's actual UAV barriers and adds no commands or barriers of its own.
 * Compile with BC250_PROBE_BARRIER_AUDIT and run with BC250_FFX_TRACE=1. */
static void replace_pointer(void* slot,void* pointer);
static UINT audit_uav_count=0;
static void (STDMETHODCALLTYPE *audit_original_barrier)(ID3D12GraphicsCommandList*,UINT,const D3D12_RESOURCE_BARRIER*)=NULL;
static void STDMETHODCALLTYPE audit_resource_barriers(ID3D12GraphicsCommandList* list,UINT count,const D3D12_RESOURCE_BARRIER* barriers) {
    for(UINT i=0;i<count;i++)if(barriers[i].Type==D3D12_RESOURCE_BARRIER_TYPE_UAV)audit_uav_count++;
    audit_original_barrier(list,count,barriers);
}
static void audit_barriers_before_dispatch(UINT frame,UINT index) {
    char line[160];
    snprintf(line,sizeof(line),"barrier_audit: frame=%u dispatch=%u preceding_uav_barriers=%u\n",frame,index,audit_uav_count);
    log_line(line);
    audit_uav_count=0;
}
static void install_barrier_audit(ID3D12GraphicsCommandList* list) {
    audit_original_barrier=list->lpVtbl->ResourceBarrier;
    replace_pointer((void*)&list->lpVtbl->ResourceBarrier,(void*)audit_resource_barriers);
}
static void remove_barrier_audit(ID3D12GraphicsCommandList* list) {
    replace_pointer((void*)&list->lpVtbl->ResourceBarrier,(void*)audit_original_barrier);
}
