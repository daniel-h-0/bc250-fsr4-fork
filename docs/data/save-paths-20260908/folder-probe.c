/* Isolated Windows known-folder probe. Reads only synthetic save canaries. */
typedef unsigned long DWORD;
typedef long HRESULT;
typedef unsigned short WCHAR;
typedef void *HANDLE;
typedef struct { unsigned long a; unsigned short b, c; unsigned char d[8]; } GUID;
__declspec(dllimport) HANDLE __stdcall GetStdHandle(DWORD);
__declspec(dllimport) int __stdcall WriteFile(HANDLE, const void *, DWORD, DWORD *, void *);
__declspec(dllimport) int __stdcall ReadFile(HANDLE, void *, DWORD, DWORD *, void *);
__declspec(dllimport) void __stdcall ExitProcess(DWORD);
__declspec(dllimport) void __stdcall Sleep(DWORD);
__declspec(dllimport) HANDLE __stdcall CreateFileW(const WCHAR *, DWORD, DWORD, void *, DWORD, DWORD, HANDLE);
__declspec(dllimport) int __stdcall CloseHandle(HANDLE);
__declspec(dllimport) DWORD __stdcall GetEnvironmentVariableW(const WCHAR *, WCHAR *, DWORD);
__declspec(dllimport) DWORD __stdcall GetModuleFileNameW(HANDLE, WCHAR *, DWORD);
__declspec(dllimport) HANDLE __stdcall LoadLibraryW(const WCHAR *);
__declspec(dllimport) void *__stdcall GetProcAddress(HANDLE, const char *);
__declspec(dllimport) int __stdcall WideCharToMultiByte(unsigned int, DWORD, const WCHAR *, int, char *, int, const char *, int *);
__declspec(dllimport) DWORD __stdcall GetFileAttributesW(const WCHAR *);
__declspec(dllimport) DWORD __stdcall GetCurrentDirectoryW(DWORD,WCHAR*);
__declspec(dllimport) DWORD __stdcall timeGetTime(void);
static HANDLE output;
static WCHAR wide[4096];
static char utf8[16384];
static const GUID ids[] = {
 {0x4c5c32ff,0xbb9d,0x43b0,{0xb5,0xb4,0x2d,0x72,0xe5,0x4e,0xaa,0xa4}},
 {0xfdd39ad0,0x238f,0x46af,{0xad,0xb4,0x6c,0x85,0x48,0x03,0x69,0xc7}},
 {0xf1b32785,0x6fba,0x4fcf,{0x9d,0x55,0x7b,0x8e,0x7f,0x15,0x70,0x91}},
 {0x3eb685db,0x65f9,0x4cf6,{0xa0,0x3a,0xe3,0xef,0x65,0x72,0x9f,0x3d}},
 {0x5e6c858f,0x0e22,0x4760,{0x9a,0xfe,0xea,0x33,0x17,0xb6,0x71,0x73}}
};
static const char *names[] = {"SavedGames","Documents","LocalAppData","RoamingAppData","Profile"};
static void text(const char *s) {
 DWORD n=0,written=0; while(s[n]) ++n;
 WriteFile(output,s,n,&written,0);
}
static void value(DWORD value) {
 char buf[11]="0x00000000";
 const char hex[]="0123456789abcdef";
 for(DWORD i=0;i<8;++i) buf[2+i]=hex[(value>>((7-i)*4))&15];
 text(buf);
}
static void string(const WCHAR *s) {
 if(!s) {text("<null>");return;}
 int n=WideCharToMultiByte(65001,0,s,-1,utf8,sizeof(utf8),0,0);
 if(n>0) text(utf8); else text("<conversion failed>");
}
void mainCRTStartup(void) {
 volatile DWORD tick=timeGetTime(); (void)tick;
 output=GetStdHandle((DWORD)-11);
 if(GetEnvironmentVariableW((const WCHAR*)L"BC250_SAVE_PROBE_OUTPUT",wide,4096)) {
   HANDLE file=CreateFileW(wide,0x40000000,3,0,2,0x80,0);
   if(file!=(HANDLE)-1) output=file;
 }
 const WCHAR *envs[]={(const WCHAR*)L"USERPROFILE",(const WCHAR*)L"USERNAME",(const WCHAR*)L"HOME",(const WCHAR*)L"WINEPREFIX",(const WCHAR*)L"STEAM_COMPAT_DATA_PATH",(const WCHAR*)L"APPDATA",(const WCHAR*)L"LOCALAPPDATA",(const WCHAR*)L"SteamAppId",(const WCHAR*)L"SteamGameId"};
 for(DWORD i=0;i<sizeof(envs)/sizeof(*envs);++i) {
   string(envs[i]);text("=");wide[0]=0;
   GetEnvironmentVariableW(envs[i],wide,4096);string(wide);text("\n");
 }
 text("Executable=");wide[0]=0;GetModuleFileNameW(0,wide,4096);string(wide);text("\n");
 text("CurrentDirectory=");wide[0]=0;GetCurrentDirectoryW(4096,wide);string(wide);text("\n");
 const WCHAR *mods[]={(const WCHAR*)L"winmm.dll",(const WCHAR*)L"shell32.dll"};
 HANDLE shell=0;
 for(DWORD i=0;i<2;++i) {
   HANDLE h=LoadLibraryW(mods[i]);
   string(mods[i]);text("=");wide[0]=0;
   if(h) GetModuleFileNameW(h,wide,4096);string(wide);text("\n");
   if(i==1) shell=h;
 }
 HRESULT (__stdcall *known)(const GUID*,DWORD,HANDLE,WCHAR**)=GetProcAddress(shell,"SHGetKnownFolderPath");
 for(DWORD i=0;i<5;++i) {
   WCHAR *path=0;
   HRESULT hr=known(&ids[i],0,0,&path);
   text(names[i]);text("=");value((DWORD)hr);text("|");string(path);
   if(hr>=0&&path) {
     DWORD n=0;while(path[n]&&n<4000){wide[n]=path[n];++n;}
     const WCHAR *suffix=(const WCHAR*)L"\\bc250-save-canary.txt";
     DWORD j=0;while(suffix[j])wide[n++]=suffix[j++];wide[n]=0;
     text("|canary=");value(GetFileAttributesW(wide));
     HANDLE file=CreateFileW(wide,0x80000000,3,0,3,0x80,0);
     text("|read=");
     if(file==(HANDLE)-1)text("missing");
     else { DWORD got=0;int ok=ReadFile(file,utf8,sizeof(utf8)-1,&got,0);utf8[got]=0;value(ok);text("|");text(utf8);CloseHandle(file); }
   }
   text("\n");
 }
 if(output!=GetStdHandle((DWORD)-11))CloseHandle(output);
 Sleep(1500);
 ExitProcess(0);
}
