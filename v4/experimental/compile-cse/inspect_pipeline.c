/* CPU-only compiler for the exact mutable image layout: no GPU work. */
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define REQUIRE(c) do {if(!(c)){fprintf(stderr,"requirement line %d\n",__LINE__);return 3;}} while(0)
#define CHECK(c) do {VkResult r=(c);if(r!=VK_SUCCESS){fprintf(stderr,"Vulkan %d line %d\n",r,__LINE__);return 2;}} while(0)
int main(int argc,char **argv){
    REQUIRE(argc==4);unsigned required=0;
    if(!strcmp(argv[2],"required32"))required=32;
    else if(!strcmp(argv[2],"required64"))required=64;
    else REQUIRE(!strcmp(argv[2],"default"));
    FILE *f=fopen(argv[1],"rb");REQUIRE(f);REQUIRE(!fseek(f,0,SEEK_END));long code_bytes=ftell(f);rewind(f);
    REQUIRE(code_bytes>0&&code_bytes%4==0&&code_bytes<16*1024*1024);uint32_t *code=malloc(code_bytes);REQUIRE(code);
    REQUIRE(fread(code,1,code_bytes,f)==(size_t)code_bytes&&fgetc(f)==EOF);REQUIRE(!fclose(f));
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo rss={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO,.requiredSubgroupSize=required};
    VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="BC250 texture interface inspection",.apiVersion=VK_API_VERSION_1_3};
    VkInstanceCreateInfo ici={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app};
    VkInstance instance; CHECK(vkCreateInstance(&ici,0,&instance));
    uint32_t count=0; CHECK(vkEnumeratePhysicalDevices(instance,&count,0)); if(!count)return 2;
    VkPhysicalDevice *devices=calloc(count,sizeof(*devices)); if(!devices)return 2;
    CHECK(vkEnumeratePhysicalDevices(instance,&count,devices));
    VkPhysicalDevice physical=VK_NULL_HANDLE; VkPhysicalDeviceProperties props;
    for(uint32_t i=0;i<count;i++){vkGetPhysicalDeviceProperties(devices[i],&props);if(props.vendorID==0x1002){physical=devices[i];break;}}
    free(devices); if(!physical)return 2;
    printf("device: %s\n",props.deviceName);
    uint32_t ext_count=0; CHECK(vkEnumerateDeviceExtensionProperties(physical,0,&ext_count,0));
    VkExtensionProperties *extensions=calloc(ext_count,sizeof(*extensions));if(!extensions)return 2;
    CHECK(vkEnumerateDeviceExtensionProperties(physical,0,&ext_count,extensions));
    int mutable_found=0,robust_found=0;
    for(uint32_t i=0;i<ext_count;i++){
        mutable_found|=!strcmp(extensions[i].extensionName,VK_EXT_MUTABLE_DESCRIPTOR_TYPE_EXTENSION_NAME);
        robust_found|=!strcmp(extensions[i].extensionName,VK_EXT_ROBUSTNESS_2_EXTENSION_NAME);
    }
    free(extensions);printf("mutable_extension: %d\nrobustness2_extension: %d\n",mutable_found,robust_found);
    if(!mutable_found||!robust_found)return 3;
    VkPhysicalDeviceMaintenance5FeaturesKHR maintenance={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MAINTENANCE_5_FEATURES_KHR};
    VkPhysicalDevicePipelineBinaryFeaturesKHR binaries={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_BINARY_FEATURES_KHR,.pNext=&maintenance};
    VkPhysicalDeviceMutableDescriptorTypeFeaturesEXT mutable={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_MUTABLE_DESCRIPTOR_TYPE_FEATURES_EXT,.pNext=&binaries};
    VkPhysicalDeviceRobustness2FeaturesEXT robust={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,.pNext=&mutable};
    VkPhysicalDeviceVulkan13Features f13={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,.pNext=&robust};
    VkPhysicalDeviceVulkan12Features f12={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,.pNext=&f13};
    VkPhysicalDeviceFeatures2 f2={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,.pNext=&f12};
    vkGetPhysicalDeviceFeatures2(physical,&f2);
#define FEATURE(name,value) printf(name ": %u\n",(unsigned)(value))
    FEATURE("mutableDescriptorType",mutable.mutableDescriptorType);
    FEATURE("runtimeDescriptorArray",f12.runtimeDescriptorArray);
    FEATURE("descriptorBindingPartiallyBound",f12.descriptorBindingPartiallyBound);
    FEATURE("shaderFloat16",f12.shaderFloat16);FEATURE("shaderInt8",f12.shaderInt8);
    FEATURE("shaderIntegerDotProduct",f13.shaderIntegerDotProduct);FEATURE("bufferDeviceAddress",f12.bufferDeviceAddress);
    FEATURE("shaderStorageImageWriteWithoutFormat",f2.features.shaderStorageImageWriteWithoutFormat);
    FEATURE("robustBufferAccess2",robust.robustBufferAccess2);FEATURE("robustImageAccess2",robust.robustImageAccess2);
    if(!f2.features.shaderInt16||!mutable.mutableDescriptorType||!f12.runtimeDescriptorArray||!f12.descriptorBindingPartiallyBound||!f12.shaderFloat16||!f12.shaderInt8||!f13.shaderIntegerDotProduct||!f12.bufferDeviceAddress||!f2.features.shaderStorageImageWriteWithoutFormat||!robust.robustBufferAccess2||!robust.robustImageAccess2)return 3;
    VkPhysicalDeviceFloatControlsProperties floats={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FLOAT_CONTROLS_PROPERTIES};
    VkPhysicalDeviceProperties2 p2={.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,.pNext=&floats};vkGetPhysicalDeviceProperties2(physical,&p2);
    FEATURE("shaderDenormPreserveFloat16",floats.shaderDenormPreserveFloat16);if(!floats.shaderDenormPreserveFloat16)return 3;
    printf("maxImageDimension2D: %u\nmaxStorageBufferRange: %u\n",props.limits.maxImageDimension2D,props.limits.maxStorageBufferRange);
    uint32_t queues_count=0;vkGetPhysicalDeviceQueueFamilyProperties(physical,&queues_count,0);
    VkQueueFamilyProperties *queues=calloc(queues_count,sizeof(*queues));if(!queues)return 2;vkGetPhysicalDeviceQueueFamilyProperties(physical,&queues_count,queues);
    uint32_t family=UINT32_MAX;for(uint32_t i=0;i<queues_count;i++)if((queues[i].queueFlags&VK_QUEUE_COMPUTE_BIT)&&queues[i].timestampValidBits){family=i;break;}if(family==UINT32_MAX)return 3;free(queues);
    REQUIRE(f2.features.shaderSampledImageArrayDynamicIndexing&&f2.features.shaderStorageImageArrayDynamicIndexing&&f2.features.shaderStorageBufferArrayDynamicIndexing);
    REQUIRE(f13.subgroupSizeControl);
    REQUIRE(binaries.pipelineBinaries&&maintenance.maintenance5);
    mutable.mutableDescriptorType=VK_TRUE;
    robust=(VkPhysicalDeviceRobustness2FeaturesEXT){.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ROBUSTNESS_2_FEATURES_EXT,.pNext=&mutable,.robustBufferAccess2=1,.robustImageAccess2=1};
    f13=(VkPhysicalDeviceVulkan13Features){.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,.pNext=&robust,.shaderIntegerDotProduct=1,.subgroupSizeControl=1};
    f12=(VkPhysicalDeviceVulkan12Features){.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,.pNext=&f13,.shaderFloat16=1,.shaderInt8=1,.runtimeDescriptorArray=1,.descriptorBindingPartiallyBound=1,.bufferDeviceAddress=1};
    f2=(VkPhysicalDeviceFeatures2){.sType=VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,.pNext=&f12,.features={.shaderInt16=1,.robustBufferAccess=1,.shaderStorageImageWriteWithoutFormat=1,.shaderSampledImageArrayDynamicIndexing=1,.shaderStorageImageArrayDynamicIndexing=1,.shaderStorageBufferArrayDynamicIndexing=1}};
    float priority=1;VkDeviceQueueCreateInfo qci={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=family,.queueCount=1,.pQueuePriorities=&priority};
    const char *names[]={VK_EXT_MUTABLE_DESCRIPTOR_TYPE_EXTENSION_NAME,VK_EXT_ROBUSTNESS_2_EXTENSION_NAME,VK_KHR_PIPELINE_BINARY_EXTENSION_NAME,VK_KHR_MAINTENANCE_5_EXTENSION_NAME};
    VkDeviceCreateInfo dci={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.pNext=&f2,.queueCreateInfoCount=1,.pQueueCreateInfos=&qci,.enabledExtensionCount=4,.ppEnabledExtensionNames=names};
    VkDevice device;CHECK(vkCreateDevice(physical,&dci,0,&device));
    VkDescriptorType types[]={VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE,VK_DESCRIPTOR_TYPE_STORAGE_IMAGE};
    VkMutableDescriptorTypeListEXT list={.descriptorTypeCount=2,.pDescriptorTypes=types};
    VkMutableDescriptorTypeCreateInfoEXT type_info={.sType=VK_STRUCTURE_TYPE_MUTABLE_DESCRIPTOR_TYPE_CREATE_INFO_EXT,.mutableDescriptorTypeListCount=1,.pMutableDescriptorTypeLists=&list};
    VkDescriptorBindingFlags flags=VK_DESCRIPTOR_BINDING_PARTIALLY_BOUND_BIT;
    VkDescriptorSetLayoutBindingFlagsCreateInfo binding_flags={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_BINDING_FLAGS_CREATE_INFO,.pNext=&type_info,.bindingCount=1,.pBindingFlags=&flags};
    VkDescriptorSetLayoutBinding image_binding={.binding=1,.descriptorType=VK_DESCRIPTOR_TYPE_MUTABLE_EXT,.descriptorCount=64,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT};
    VkDescriptorSetLayoutCreateInfo li={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};VkDescriptorSetLayout layouts[3];
    CHECK(vkCreateDescriptorSetLayout(device,&li,0,&layouts[0]));li.pNext=&binding_flags;li.bindingCount=1;li.pBindings=&image_binding;
    VkDescriptorSetLayoutSupport support={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_SUPPORT};vkGetDescriptorSetLayoutSupport(device,&li,&support);REQUIRE(support.supported);
    CHECK(vkCreateDescriptorSetLayout(device,&li,0,&layouts[1]));
    VkDescriptorSetLayoutBinding buffer_binding={.binding=0,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=64,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT};
    li.pNext=0;li.pBindings=&buffer_binding;CHECK(vkCreateDescriptorSetLayout(device,&li,0,&layouts[2]));
    VkPushConstantRange push_range={.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT,.size=16};
    VkPipelineLayoutCreateInfo pli={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,.setLayoutCount=3,.pSetLayouts=layouts,.pushConstantRangeCount=1,.pPushConstantRanges=&push_range};
    VkPipelineLayout layout;CHECK(vkCreatePipelineLayout(device,&pli,0,&layout));
    VkShaderModuleCreateInfo smi={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=(size_t)code_bytes,.pCode=code};VkShaderModule module;CHECK(vkCreateShaderModule(device,&smi,0,&module));free(code);
    VkComputePipelineCreateInfo pci={.sType=VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,.stage={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,.pNext=required?&rss:NULL,.stage=VK_SHADER_STAGE_COMPUTE_BIT,.module=module,.pName="main"},.layout=layout};
    VkPipelineCreateFlags2CreateInfoKHR flags2={.sType=VK_STRUCTURE_TYPE_PIPELINE_CREATE_FLAGS_2_CREATE_INFO_KHR,.flags=VK_PIPELINE_CREATE_2_CAPTURE_DATA_BIT_KHR};pci.pNext=&flags2;
    VkPipeline pipeline;CHECK(vkCreateComputePipelines(device,0,1,&pci,0,&pipeline));


    PFN_vkCreatePipelineBinariesKHR create_binaries=(void*)vkGetDeviceProcAddr(device,"vkCreatePipelineBinariesKHR");
    PFN_vkGetPipelineBinaryDataKHR get_binary=(void*)vkGetDeviceProcAddr(device,"vkGetPipelineBinaryDataKHR");
    PFN_vkDestroyPipelineBinaryKHR destroy_binary=(void*)vkGetDeviceProcAddr(device,"vkDestroyPipelineBinaryKHR");
    REQUIRE(create_binaries&&get_binary&&destroy_binary);
    VkPipelineBinaryCreateInfoKHR bi={.sType=VK_STRUCTURE_TYPE_PIPELINE_BINARY_CREATE_INFO_KHR,.pipeline=pipeline};
    VkPipelineBinaryHandlesInfoKHR handles={.sType=VK_STRUCTURE_TYPE_PIPELINE_BINARY_HANDLES_INFO_KHR};
    CHECK(create_binaries(device,&bi,0,&handles));REQUIRE(handles.pipelineBinaryCount==1);
    VkPipelineBinaryKHR handle;handles.pPipelineBinaries=&handle;CHECK(create_binaries(device,&bi,0,&handles));
    VkPipelineBinaryDataInfoKHR di={.sType=VK_STRUCTURE_TYPE_PIPELINE_BINARY_DATA_INFO_KHR,.pipelineBinary=handle};
    VkPipelineBinaryKeyKHR key={.sType=VK_STRUCTURE_TYPE_PIPELINE_BINARY_KEY_KHR};size_t size=0;
    CHECK(get_binary(device,&di,&key,&size,0));REQUIRE(size>0&&size<64*1024*1024);
    void *data=malloc(size);REQUIRE(data);CHECK(get_binary(device,&di,&key,&size,data));
    FILE *output=fopen(argv[3],"wbx");REQUIRE(output);REQUIRE(fwrite(data,1,size,output)==size);REQUIRE(!fclose(output));free(data);
    printf("pipeline_binary_bytes: %zu\n",size);destroy_binary(device,handle,0);

    printf("pipeline_compile_only: PASS\nrequested_subgroup: %u\n",required);
    vkDestroyPipeline(device,pipeline,0);vkDestroyShaderModule(device,module,0);vkDestroyPipelineLayout(device,layout,0);
    for(unsigned i=0;i<3;i++)vkDestroyDescriptorSetLayout(device,layouts[i],0);
    vkDestroyDevice(device,0);vkDestroyInstance(instance,0);return 0;
}
