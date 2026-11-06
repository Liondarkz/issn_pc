#include "runtime.h"
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
typedef int32_t (ABI *Module)(uint16_t);
typedef int32_t (ABI *Init)(const void *,void *);
typedef int32_t (ABI *Param)(uint32_t,int32_t *);
typedef int32_t (ABI *List)(uint32_t,void *,uint32_t,uint32_t *);
typedef int32_t (ABI *Info)(uint32_t,const void *,void *);
#define GET(t,n) ((t)runtime_content_resolve(n))
int main(int argc,char **argv) {
    Module load=GET(Module,"g8cM39EUZ6o#libSceSysmodule");
    Module loaded=GET(Module,"fMP5NHUOaMk#libSceSysmodule");
    Module unload=GET(Module,"eR2bZFAAU0Q#libSceSysmodule");
    Init init=GET(Init,"R9lA82OraNs#libSceAppContent");
    Param param=GET(Param,"99b82IKXpH4#libSceAppContent");
    List list=GET(List,"xnd8BJzAxmk#libSceAppContent");
    assert(load && loaded && unload && init && param && list);
    assert(!runtime_content_resolve("g8cM39EUZ6o#libkernel"));
    Info info=GET(Info,"m47juOmH0VE#libSceAppContent");
    assert(info);
    if (argc>1 && !strcmp(argv[1],"--missing")) { load(0xb4); return 99; }
    if (argc>1 && !strcmp(argv[1],"--addons")) {
        /* issn 11-06: labels from the player's dumps; the game's own license is refused. */
        const uint32_t full[]={3,0,0,0,0};
        runtime_content_configure(full);
        const char bad[1][16]={{'U','N','L','O','C','K','A','P','P','L','I','C','A','T','I','O'}};
        assert(runtime_content_addons(bad,1)==-1);
        const char lower[1][16]={{'u','n','l','o','c','k'}};
        assert(runtime_content_addons(lower,1)==-1);
        char labels[2][16];
        memcpy(labels[0],"UNLOCKCOLEMISSIO",16); memcpy(labels[1],"UNLOCKVESTSKELET",16);
        assert(runtime_content_addons(labels,2)==0);
        unsigned char none[32]={0}, boot[40];
        assert(load(0xb4)==0 && init(none,boot)==0);
        struct { char data[17]; char pad[3]; } label={"UNLOCKCOLEMISSIO",{0}};
        struct { unsigned char bytes[24]; uint32_t canary; } out;
        memset(&out,0xaa,sizeof(out));
        assert(info(0,&label,out.bytes)==0);
        assert(!memcmp(out.bytes,"UNLOCKCOLEMISSIO",16) && out.bytes[16]==0);
        uint32_t status; memcpy(&status,out.bytes+0x14,4);
        assert(status==4 && out.canary==0xaaaaaaaa);
        assert(info(1,&label,out.bytes)==(int32_t)0x80d90007);
        memcpy(label.data,"UNLOCKVESTJOS000",16);
        memset(&out,0xaa,sizeof(out));
        assert(info(0,&label,out.bytes)==(int32_t)0x80d90007 && out.bytes[0]==0xaa);
        assert(info(0,&label,out.bytes)==(int32_t)0x80d90007);
        uint32_t hits=0;
        assert(list(0,NULL,0,&hits)==0 && hits==2);
        unsigned char entries[3*24+4]; memset(entries,0xbb,sizeof(entries));
        assert(list(0,entries,1,&hits)==0 && hits==1 && entries[24]==0xbb);
        assert(!memcmp(entries,"UNLOCKCOLEMISSIO",16));
        assert(list(0,entries,3,&hits)==0 && hits==2 && !memcmp(entries+24,"UNLOCKVESTSKELET",16) && entries[48]==0xbb);
        puts("PASS: add-ons answer only their labels"); return 0;
    }
    const uint32_t profile[]={1,13,0x80000000,0,7};
    runtime_content_configure(profile);
    if (argc>1 && !strcmp(argv[1],"--unknown")) {
        /* Other system modules are host-provided: reference counted, independent. */
        assert((uint32_t)loaded(0xb5)==0x805a1001);
        assert(load(0xb5)==0 && loaded(0xb5)==0 && (uint32_t)loaded(0xb4)==0x805a1001);
        assert(unload(0xb5)==0 && (uint32_t)loaded(0xb5)==0x805a1001);
        puts("PASS: independent module references"); return 0;
    }
    assert((uint32_t)loaded(0)==0x805a1000);
    assert((uint32_t)loaded(0xb4)==0x805a1001);
    assert((uint32_t)unload(0xb4)==0x805a1001);
    assert(load(0xb4)==0 && load(0xb4)==0 && loaded(0xb4)==0);
    unsigned char initial[32]={0};
    struct { unsigned char boot[40]; uint32_t canary; } b;
    memset(&b,0xa5,sizeof(b));
    assert((uint32_t)init(NULL,b.boot)==0x80d90002);
    initial[31]=1; assert((uint32_t)init(initial,b.boot)==0x80d90002); initial[31]=0;
    assert(b.boot[0]==0xa5 && init(initial,b.boot)==0);
    for (unsigned i=0;i<40;++i) assert(b.boot[i]==0);
    assert(b.canary==0xa5a5a5a5);
    b.boot[0]=0xcc; assert((uint32_t)init(initial,b.boot)==0x80d90003 && b.boot[0]==0xcc);
    struct { int32_t value; uint32_t canary; } result={-1,0x1234};
    for (unsigned i=0;i<5;++i) {
        assert(param(i,&result.value)==0 && (uint32_t)result.value==profile[i]);
        assert(result.canary==0x1234);
    }
    result.value=77;
    assert((uint32_t)param(5,&result.value)==0x80d90002 && result.value==77);
    assert((uint32_t)param(0,NULL)==0x80d90002);
    unsigned char entries[48]; memset(entries,0xaa,sizeof(entries));
    struct { uint32_t hits,canary; } h={99,0xabcdef};
    assert(list(0,NULL,0,&h.hits)==0 && h.hits==0 && h.canary==0xabcdef);
    assert(list(0,entries,2,&h.hits)==0 && h.hits==0);
    for (unsigned i=0;i<sizeof(entries);++i) assert(entries[i]==0xaa);
    assert((uint32_t)list(0,NULL,0,NULL)==0x80d90002);
    assert(unload(0xb4)==0 && loaded(0xb4)==0);
    assert((uint32_t)init(initial,b.boot)==0x80d90003);
    assert(unload(0xb4)==0 && (uint32_t)loaded(0xb4)==0x805a1001);
    assert(load(0xb4)==0 && init(initial,b.boot)==0 && unload(0xb4)==0);
    puts("PASS: AppContent lifecycle, profile and output boundaries");
    return 0;
}
