#include "symtab.h"
#include <string.h>

/* リンカスクリプトが定義する名前表の範囲（.sym_table セクション） */
extern const sym_entry_t __sym_table_start[];
extern const sym_entry_t __sym_table_end[];

uint16_t symtab_count(void)
{
    return (uint16_t)(__sym_table_end - __sym_table_start);
}

const sym_entry_t *symtab_get(uint16_t index)
{
    if (index >= symtab_count()) {
        return 0;
    }
    return &__sym_table_start[index];
}

uint8_t symtab_name_len(const sym_entry_t *e)
{
    return (uint8_t)strlen(e->name);
}

int32_t symtab_find(const uint8_t *name, uint8_t nlen)
{
    uint16_t n = symtab_count();
    for (uint16_t i = 0; i < n; i++) {
        const sym_entry_t *e = &__sym_table_start[i];
        if (strlen(e->name) == nlen && memcmp(e->name, name, nlen) == 0) {
            return i;
        }
    }
    return -1;
}

uint32_t symtab_size(const sym_entry_t *e)
{
    uint32_t elem;
    switch (e->type) {
    case SYM_U16:
    case SYM_I16:
        elem = 2u;
        break;
    case SYM_U32:
    case SYM_I32:
    case SYM_F32:
        elem = 4u;
        break;
    default:   /* SYM_U8, SYM_I8, SYM_BYTES */
        elem = 1u;
        break;
    }
    return elem * e->num;
}
