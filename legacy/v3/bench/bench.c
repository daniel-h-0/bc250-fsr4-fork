#define _GNU_SOURCE
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#define WG 4096
#define LS 64
#define NELEM (WG * LS)

static int ITER = 16384, UNROLL = 16, FAST = 0;
#define TOTAL_DOTS ((uint64_t)NELEM * (uint64_t)ITER * (uint64_t)UNROLL)

#define CHECK(x) do { \
    VkResult r_ = (x); \
    if (r_ != VK_SUCCESS) { \
        fprintf(stderr, "Vulkan error %d at %s:%d\n", r_, __FILE__, __LINE__); \
        exit(1); \
    } \
} while (0)

static double now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

static int cmpd(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static uint32_t dot_one(uint32_t a, uint32_t b, int as_signed, int bs_signed)
{
    int64_t s = 0;
    for (int i = 0; i < 4; i++) {
        int64_t av = as_signed ? (int8_t)((a >> (8 * i)) & 0xff) : (uint8_t)((a >> (8 * i)) & 0xff);
        int64_t bv = bs_signed ? (int8_t)((b >> (8 * i)) & 0xff) : (uint8_t)((b >> (8 * i)) & 0xff);
        s += av * bv;
    }
    return (uint32_t)s;
}

static uint32_t rep(uint32_t x)
{
    uint32_t b = x & 255;
    return b | (b << 8) | (b << 16) | (b << 24);
}

static uint32_t dot_cpu(const uint32_t *A, const uint32_t *B, uint32_t lane, const char *kind)
{
    int as_signed = 1, bs_signed = 1;
    if (!strcmp(kind, "udot")) {
        as_signed = 0;
        bs_signed = 0;
    } else if (!strcmp(kind, "sudot")) {
        bs_signed = 0;
    }

    uint32_t acc = 0;
    const uint32_t *pa = A + (size_t)lane * UNROLL;
    const uint32_t *pb = B + (size_t)lane * UNROLL;

    for (uint32_t i = 0; i < (uint32_t)ITER; i++) {
        uint32_t m = rep(i);
        for (int k = 0; k < UNROLL; k++)
            acc += dot_one(pa[k] ^ m, pb[k], as_signed, bs_signed);
    }
    return acc;
}

static uint32_t find_memory_type(const VkPhysicalDeviceMemoryProperties *mp,
                                 uint32_t memory_type_bits,
                                 VkMemoryPropertyFlags required)
{
    for (uint32_t i = 0; i < mp->memoryTypeCount; i++) {
        if (!(memory_type_bits & (1u << i)))
            continue;
        if ((mp->memoryTypes[i].propertyFlags & required) == required)
            return i;
    }
    return UINT32_MAX;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s kernel.spv sdot|udot|sudot [ITER] [UNROLL] [fast]\n", argv[0]);
        return 2;
    }
    if (argc > 3) ITER = atoi(argv[3]);
    if (argc > 4) UNROLL = atoi(argv[4]);
    if (argc > 5) FAST = 1;

    const char *kind = argv[2];
    if (strcmp(kind, "sdot") && strcmp(kind, "udot") && strcmp(kind, "sudot")) {
        fprintf(stderr, "invalid kernel kind: %s\n", kind);
        return 2;
    }

    VkApplicationInfo ai = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .apiVersion = VK_API_VERSION_1_3,
    };
    VkInstanceCreateInfo ici = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &ai,
    };
    VkInstance inst;
    CHECK(vkCreateInstance(&ici, NULL, &inst));

    uint32_t np = 0;
    CHECK(vkEnumeratePhysicalDevices(inst, &np, NULL));
    if (!np) {
        fprintf(stderr, "no Vulkan physical devices\n");
        return 1;
    }
    VkPhysicalDevice *pds = malloc(np * sizeof(*pds));
    CHECK(vkEnumeratePhysicalDevices(inst, &np, pds));

    VkPhysicalDevice pd = VK_NULL_HANDLE;
    for (uint32_t i = 0; i < np; i++) {
        VkPhysicalDeviceProperties p;
        vkGetPhysicalDeviceProperties(pds[i], &p);
        if (p.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) {
            pd = pds[i];
            break;
        }
    }
    if (!pd)
        pd = pds[0];

    VkPhysicalDeviceDriverProperties dp = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES,
    };
    VkPhysicalDeviceProperties2 p2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
        .pNext = &dp,
    };
    vkGetPhysicalDeviceProperties2(pd, &p2);
    printf("device: %s\n", p2.properties.deviceName);
    printf("driver: %s %s\n", dp.driverName, dp.driverInfo);

    VkPhysicalDeviceShaderIntegerDotProductFeatures dot_support = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_INTEGER_DOT_PRODUCT_FEATURES,
    };
    VkPhysicalDeviceFeatures2 features2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &dot_support,
    };
    vkGetPhysicalDeviceFeatures2(pd, &features2);
    if (!dot_support.shaderIntegerDotProduct) {
        fprintf(stderr, "shaderIntegerDotProduct is not supported\n");
        return 1;
    }

    uint32_t qn = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, NULL);
    VkQueueFamilyProperties *qp = malloc(qn * sizeof(*qp));
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qn, qp);
    uint32_t qf = UINT32_MAX;
    for (uint32_t i = 0; i < qn; i++) {
        if (qp[i].queueFlags & VK_QUEUE_COMPUTE_BIT) {
            qf = i;
            break;
        }
    }
    if (qf == UINT32_MAX) {
        fprintf(stderr, "no compute queue\n");
        return 1;
    }

    float prio = 0.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = qf,
        .queueCount = 1,
        .pQueuePriorities = &prio,
    };
    VkPhysicalDeviceShaderIntegerDotProductFeatures dot_enable = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_INTEGER_DOT_PRODUCT_FEATURES,
        .shaderIntegerDotProduct = VK_TRUE,
    };
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext = &dot_enable,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
    };
    VkDevice dev;
    CHECK(vkCreateDevice(pd, &dci, NULL, &dev));

    VkQueue queue;
    vkGetDeviceQueue(dev, qf, 0, &queue);

    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd, &mp);

    VkBuffer bufs[3];
    VkDeviceMemory mems[3];
    uint32_t *maps[3];
    size_t szAB = (size_t)NELEM * UNROLL * sizeof(uint32_t);
    size_t szO = (size_t)NELEM * sizeof(uint32_t);
    size_t szs[3] = {szAB, szAB, szO};

    for (int i = 0; i < 3; i++) {
        VkBufferCreateInfo bi = {
            .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
            .size = szs[i],
            .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
            .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        };
        CHECK(vkCreateBuffer(dev, &bi, NULL, &bufs[i]));

        VkMemoryRequirements mr;
        vkGetBufferMemoryRequirements(dev, bufs[i], &mr);
        uint32_t mt = find_memory_type(&mp, mr.memoryTypeBits,
            VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
        if (mt == UINT32_MAX) {
            fprintf(stderr, "no compatible HOST_VISIBLE|HOST_COHERENT memory for buffer %d\n", i);
            return 1;
        }

        VkMemoryAllocateInfo mai = {
            .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
            .allocationSize = mr.size,
            .memoryTypeIndex = mt,
        };
        CHECK(vkAllocateMemory(dev, &mai, NULL, &mems[i]));
        CHECK(vkBindBufferMemory(dev, bufs[i], mems[i], 0));
        CHECK(vkMapMemory(dev, mems[i], 0, szs[i], 0, (void **)&maps[i]));
    }

    for (uint32_t i = 0; i < (uint32_t)NELEM * (uint32_t)UNROLL; i++) {
        maps[0][i] = i * 2654435761u + 1u;
        maps[1][i] = i * 2246822519u + 7u;
    }
    for (uint32_t i = 0; i < NELEM; i++)
        maps[2][i] = 0;

    VkDescriptorSetLayoutBinding lb[3];
    for (int i = 0; i < 3; i++) {
        lb[i] = (VkDescriptorSetLayoutBinding){
            .binding = (uint32_t)i,
            .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .descriptorCount = 1,
            .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT,
        };
    }
    VkDescriptorSetLayoutCreateInfo dsl = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 3,
        .pBindings = lb,
    };
    VkDescriptorSetLayout dslh;
    CHECK(vkCreateDescriptorSetLayout(dev, &dsl, NULL, &dslh));

    VkPipelineLayoutCreateInfo pli = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1,
        .pSetLayouts = &dslh,
    };
    VkPipelineLayout pl;
    CHECK(vkCreatePipelineLayout(dev, &pli, NULL, &pl));

    FILE *f = fopen(argv[1], "rb");
    if (!f) {
        perror("spv");
        return 1;
    }
    fseek(f, 0, SEEK_END);
    long fl = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (fl <= 0 || (fl & 3)) {
        fprintf(stderr, "invalid SPIR-V byte size: %ld\n", fl);
        return 1;
    }
    uint32_t *code = malloc((size_t)fl);
    if (fread(code, 1, (size_t)fl, f) != (size_t)fl)
        return 1;
    fclose(f);

    VkShaderModuleCreateInfo smi = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = (size_t)fl,
        .pCode = code,
    };
    VkShaderModule sm;
    CHECK(vkCreateShaderModule(dev, &smi, NULL, &sm));

    VkComputePipelineCreateInfo cpi = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT,
            .module = sm,
            .pName = "main",
        },
        .layout = pl,
    };
    VkPipeline pipe;
    CHECK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpi, NULL, &pipe));

    VkDescriptorPoolSize ps = {
        .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
        .descriptorCount = 3,
    };
    VkDescriptorPoolCreateInfo dpi = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1,
        .poolSizeCount = 1,
        .pPoolSizes = &ps,
    };
    VkDescriptorPool pool;
    CHECK(vkCreateDescriptorPool(dev, &dpi, NULL, &pool));

    VkDescriptorSetAllocateInfo dai = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = pool,
        .descriptorSetCount = 1,
        .pSetLayouts = &dslh,
    };
    VkDescriptorSet set;
    CHECK(vkAllocateDescriptorSets(dev, &dai, &set));

    VkWriteDescriptorSet w[3];
    VkDescriptorBufferInfo binfo[3];
    for (int i = 0; i < 3; i++) {
        binfo[i] = (VkDescriptorBufferInfo){
            .buffer = bufs[i],
            .offset = 0,
            .range = szs[i],
        };
        w[i] = (VkWriteDescriptorSet){
            .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
            .dstSet = set,
            .dstBinding = (uint32_t)i,
            .descriptorCount = 1,
            .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .pBufferInfo = &binfo[i],
        };
    }
    vkUpdateDescriptorSets(dev, 3, w, 0, NULL);

    VkCommandPoolCreateInfo cpci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
        .queueFamilyIndex = qf,
    };
    VkCommandPool cpool;
    CHECK(vkCreateCommandPool(dev, &cpci, NULL, &cpool));

    VkCommandBufferAllocateInfo cai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = cpool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    VkCommandBuffer cb;
    CHECK(vkAllocateCommandBuffers(dev, &cai, &cb));

    /* This command buffer is deliberately reusable: it is submitted repeatedly
     * for warmup and timing, so do not mark it ONE_TIME_SUBMIT. */
    VkCommandBufferBeginInfo cbi = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = 0,
    };
    CHECK(vkBeginCommandBuffer(cb, &cbi));
    vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
    vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pl, 0, 1, &set, 0, NULL);
    vkCmdDispatch(cb, WG, 1, 1);
    CHECK(vkEndCommandBuffer(cb));

    VkFenceCreateInfo fi = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence;
    CHECK(vkCreateFence(dev, &fi, NULL, &fence));

    VkSubmitInfo si = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &cb,
    };

    double w0 = now_ms();
    if (!FAST) {
        while (now_ms() - w0 < 1500.0) {
            CHECK(vkResetFences(dev, 1, &fence));
            CHECK(vkQueueSubmit(queue, 1, &si, fence));
            CHECK(vkWaitForFences(dev, 1, &fence, VK_TRUE, UINT64_MAX));
        }
    }

    const int reps = FAST ? 1 : 10;
    double times[10];
    for (int r = 0; r < reps; r++) {
        CHECK(vkResetFences(dev, 1, &fence));
        double t0 = now_ms();
        CHECK(vkQueueSubmit(queue, 1, &si, fence));
        CHECK(vkWaitForFences(dev, 1, &fence, VK_TRUE, UINT64_MAX));
        times[r] = now_ms() - t0;
    }

    FILE *sf = fopen("/sys/class/drm/card0/device/pp_dpm_sclk", "r");
    if (!sf)
        sf = fopen("/sys/class/drm/card1/device/pp_dpm_sclk", "r");
    if (sf) {
        char line[256];
        printf("sclk: ");
        while (fgets(line, sizeof(line), sf)) {
            if (strstr(line, "*"))
                printf("%s", line);
        }
        fclose(sf);
    }

    qsort(times, (size_t)reps, sizeof(double), cmpd);
    double med = times[reps / 2], best = times[0];
    printf("kind: %s  median: %.2f ms  best: %.2f ms  throughput: %.2f Gdot/s\n",
           kind, med, best, (TOTAL_DOTS / 1e9) / (best / 1e3));

    int bad = 0;
    uint32_t nsamp = NELEM < 64 ? NELEM : 64;
    for (uint32_t i = 0; i < nsamp && bad < 5; i++) {
        uint32_t expected = dot_cpu(maps[0], maps[1], i, kind);
        if (maps[2][i] != expected) {
            if (!bad)
                fprintf(stderr, "MISMATCH i=%u got=%u exp=%u\n", i, maps[2][i], expected);
            bad++;
        }
    }
    printf("verify: %s\n", bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
