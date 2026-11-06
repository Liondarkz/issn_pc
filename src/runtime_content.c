/* Offline AppContent provider for the configured base-game profile.
 * No package mounting, downloads or license emulation. issn 11-06: add-ons are the entitlement
 * labels content_profile.py read from the param.sfo of the player's own add-on dumps; inFAMOUS
 * Second Son only asks whether a label exists (claude/dlc-estudo.md). */
#include "runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define INVALID_ID ((int32_t)0x805a1000)
#define NOT_LOADED ((int32_t)0x805a1001)
#define PARAMETER ((int32_t)0x80d90002)
#define BUSY ((int32_t)0x80d90003)
static unsigned refs, initialized, configured;
static uint32_t parameters[5];
static unsigned queries, lists;
/* issn 11-06: add-on labels (16 characters; the label field holds 17 with its terminator). */
#define ADDON_MAX 8
#define LABEL_CHARS 16
static char addon_labels[ADDON_MAX][LABEL_CHARS+1];
static unsigned addon_count;
static unsigned char addon_reported[ADDON_MAX];
static char asked_missing[ADDON_MAX][LABEL_CHARS+1];
static unsigned asked_missing_count;
void runtime_content_configure(const uint32_t values[5]) {
    if (refs || initialized || (values[0]!=1 && values[0]!=3)) {
        fputs("ERROR: invalid or late content profile\n",stderr); exit(1);
    }
    memcpy(parameters,values,sizeof(parameters)); configured=1;
}
int runtime_content_addons(const char (*labels)[LABEL_CHARS], unsigned count) {
    if (refs || initialized || count>ADDON_MAX) return -1;
    for (unsigned i=0;i<count;++i) {
        char label[LABEL_CHARS+1]={0};
        memcpy(label,labels[i],LABEL_CHARS);
        size_t n=strlen(label);
        if (!n) return -1;
        for (size_t k=0;k<n;++k) if (label[k]<'0' || label[k]>'Z' || (label[k]>'9' && label[k]<'A')) return -1;
        /* The game's own license, not an add-on: never answered. */
        if (!strcmp(label,"UNLOCKAPPLICATIO")) return -1;
        memcpy(addon_labels[i],label,sizeof(label));
        printf("Runtime: AppContent add-on %s installed (player's dump)\n",label);
    }
    addon_count=count;
    return 0;
}
static void require_provider(void) {
    if (!configured) { fputs("STOP: AppContent needs --content-profile\n",stderr); exit(21); }
}
/* System modules: every library this eboot imports is provided by the host
 * runtime, so loading one only tracks a reference count. AppContent (0xb4)
 * additionally requires the configured content profile. */
static unsigned module_refs[0x10000];
static ABI int32_t module_load(uint16_t id) {
    if (!id) return INVALID_ID;
    if (id==0xb4) require_provider();
    if (module_refs[id]==UINT32_MAX) { fputs("STOP: module reference overflow\n",stderr); exit(21); }
    ++module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; printf("Runtime: AppContent host provider loaded; references=%u\n",refs); }
    else printf("Runtime: system module 0x%x loaded (host implementation)\n",id);
    return 0;
}
static ABI int32_t module_loaded(uint16_t id) {
    if (!id) return INVALID_ID;
    return module_refs[id] ? 0 : NOT_LOADED;
}
static ABI int32_t module_unload(uint16_t id) {
    int32_t result=module_loaded(id);
    if (result) return result;
    --module_refs[id];
    if (id==0xb4) { refs=module_refs[id]; if (!refs) initialized=0; }
    return 0;
}
static void require_loaded(void) {
    require_provider();
    if (!refs) { fputs("STOP: AppContent called before module load\n",stderr); exit(21); }
}
static ABI int32_t content_init(const unsigned char *init,unsigned char *boot) {
    require_loaded();
    if (!init || !boot) return PARAMETER;
    if (initialized) return BUSY;
    /* Only the reserved-zero init profile is implemented. */
    for (unsigned i=0;i<32;++i) if (init[i]) return PARAMETER;
    memset(boot,0,40); initialized=1;
    if (addon_count) printf("Runtime: AppContent initialized; offline base-game profile, %u add-on(s) from the player's dumps\n",addon_count);
    else puts("Runtime: AppContent initialized; offline base-game profile, no mounted add-ons");
    return 0;
}
static void require_initialized(void) {
    require_loaded();
    if (!initialized) { fputs("STOP: AppContent query before initialization\n",stderr); exit(21); }
}
static ABI int32_t param_int(uint32_t id,int32_t *out) {
    require_initialized();
    if (id>4 || !out) return PARAMETER;
    memcpy(out,&parameters[id],4); ++queries;
    printf("Runtime: AppContent parameter %u = %d\n",id,*out);
    return 0;
}
/* Add-on information: the entitlement label (17 bytes + 3 of padding) and the status at +0x14,
 * where inFAMOUS Second Son reads it (0x571930: unlocked when the status is 0 or 4). */
#define INFO_SIZE 24
#define STATUS_INSTALLED 4u
static void fill_info(unsigned char *info,unsigned index) {
    uint32_t status=STATUS_INSTALLED;
    memset(info,0,INFO_SIZE);
    memcpy(info,addon_labels[index],LABEL_CHARS);
    memcpy(info+0x14,&status,4);
}
static ABI int32_t addon_list(uint32_t service,void *list,uint32_t capacity,uint32_t *hits) {
    require_initialized();
    if (service) { fputs("STOP: nonzero AppContent service label unsupported\n",stderr); exit(21); }
    if ((!capacity || !list) && !hits) return PARAMETER;
    /* Never touch list slots past what is written. */
    unsigned written=0;
    if (list && capacity)
        for (; written<addon_count && written<capacity; ++written) fill_info((unsigned char *)list+written*INFO_SIZE,written);
    if (hits) *hits=(list && capacity) ? written : addon_count;
    ++lists; return 0;
}
/* issn: one add-on's information (inFAMOUS Second Son asks for its DLC by label). */
#define NO_ENTITLEMENT ((int32_t)0x80d90007)
static ABI int32_t addon_info(uint32_t service,const void *label,void *info) {
    require_initialized();
    if (!label || !info) return PARAMETER;
    ++queries;
    char asked[LABEL_CHARS+1]={0};
    memcpy(asked,label,LABEL_CHARS);
    for (unsigned k=0;asked[k];++k) if (asked[k]<0x20 || asked[k]>0x7e) asked[k]='?';
    for (unsigned i=0;!service && i<addon_count;++i) {
        if (strncmp(asked,addon_labels[i],LABEL_CHARS)) continue;
        if (!addon_reported[i]) { addon_reported[i]=1; printf("Runtime: AppContent game asked for add-on %s: installed\n",asked); }
        fill_info(info,i);
        return 0;
    }
    /* Reported once per label: the game asks again whenever it recounts its unlocks. */
    unsigned seen=0;
    for (unsigned i=0;i<asked_missing_count;++i) if (!strcmp(asked_missing[i],asked)) seen=1;
    if (!seen && asked_missing_count<ADDON_MAX) {
        memcpy(asked_missing[asked_missing_count++],asked,sizeof(asked));
        printf("Runtime: AppContent game asked for add-on %s (service %u): not installed\n",asked,service);
    }
    return NO_ENTITLEMENT;
}
uintptr_t runtime_content_resolve(const char *name) {
    if (!strcmp(name,"m47juOmH0VE#libSceAppContent")) return (uintptr_t)addon_info;
    if (!strcmp(name,"g8cM39EUZ6o#libSceSysmodule")) return (uintptr_t)module_load;
    if (!strcmp(name,"fMP5NHUOaMk#libSceSysmodule")) return (uintptr_t)module_loaded;
    if (!strcmp(name,"eR2bZFAAU0Q#libSceSysmodule")) return (uintptr_t)module_unload;
    if (!strcmp(name,"R9lA82OraNs#libSceAppContent")) return (uintptr_t)content_init;
    if (!strcmp(name,"99b82IKXpH4#libSceAppContent")) return (uintptr_t)param_int;
    if (!strcmp(name,"xnd8BJzAxmk#libSceAppContent")) return (uintptr_t)addon_list;
    return 0;
}
void runtime_content_report(void) {
    printf("Runtime: AppContent references=%u, initialized=%u, parameter queries=%u, list queries=%u, add-ons=%u\n",refs,initialized,queries,lists,addon_count);
}
