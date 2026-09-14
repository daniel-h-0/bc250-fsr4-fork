/* SPDX-License-Identifier: MIT
 * Optional CPU startup timing. No GPU commands or shader changes.
 * Use only for startup studies, separately from steady-state campaigns. */
static void startup_mark(const char* phase) {
    LARGE_INTEGER counter, frequency;
    char line[192];
    if (!QueryPerformanceCounter(&counter) || !QueryPerformanceFrequency(&frequency))
        ExitProcess(23);
    snprintf(line, sizeof(line), "startup_cpu: phase=%s counter=%lld frequency=%lld\n",
             phase, (long long)counter.QuadPart, (long long)frequency.QuadPart);
    log_line(line);
}
