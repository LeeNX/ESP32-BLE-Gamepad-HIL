/*
 * hil_slots.h -- OTA app-slot query and switch, shared by hil_runner (Arduino, C++) and the Bluepad32 observer
 * (ESP-IDF, C). Multi-slot PoC: docs/multislot-ota.md. Header-only, everything static.
 *
 * hil_slots_describe() fills one line body:
 *
 *   running=<n> boot=<n> count=<n> <i>=<sha256>/<state>/<project>/<version> ... | <i>=empty
 *
 * <sha256> is esp_partition_get_sha256() of the slot: for an app image with an appended hash (every image here) that
 * is the 32-byte digest at the image's end, which builder/make_multislot.py records per slot as image_sha256 -- the
 * host checks slot contents without reading flash back over USB. Reading it verifies the whole image (~0.6 MB per
 * slot), so the line takes a few hundred ms.
 *
 * hil_slots_set_boot() selects the slot the next boot starts (esp_ota_set_boot_partition(), which verifies the
 * image first) and optionally erases NVS -- BLE bonds -- for a board changing role. The caller replies, flushes its
 * console and calls hil_slots_restart().
 *
 * With rollback enabled in the bootloader a slot switched to this way boots in PENDING_VERIFY: the new app must call
 * esp_ota_mark_app_valid_cancel_rollback() (Arduino does in initArduino(); the observer does once Bluetooth is up),
 * or the bootloader returns to the previous slot on the next reset.
 */
#pragma once

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <esp_system.h>

static const esp_partition_t *hil_slot_partition(int idx)
{
    if (idx < 0 || idx > 15) return NULL;
    return esp_partition_find_first(ESP_PARTITION_TYPE_APP,
                                    (esp_partition_subtype_t)(ESP_PARTITION_SUBTYPE_APP_OTA_MIN + idx), NULL);
}

static int hil_slot_count(void)
{
    int n = 0;
    while (hil_slot_partition(n)) n++;
    return n;
}

// Slot index of a partition, or -1 (factory / not an OTA slot).
static int hil_slot_index(const esp_partition_t *p)
{
    if (!p || p->type != ESP_PARTITION_TYPE_APP) return -1;
    if (p->subtype < ESP_PARTITION_SUBTYPE_APP_OTA_MIN || p->subtype > ESP_PARTITION_SUBTYPE_APP_OTA_MAX) return -1;
    return (int)p->subtype - ESP_PARTITION_SUBTYPE_APP_OTA_MIN;
}

static const char *hil_slot_state_name(const esp_partition_t *p)
{
    esp_ota_img_states_t s;
    if (esp_ota_get_state_partition(p, &s) != ESP_OK) return "none";
    switch (s)
    {
    case ESP_OTA_IMG_NEW: return "new";
    case ESP_OTA_IMG_PENDING_VERIFY: return "pending";
    case ESP_OTA_IMG_VALID: return "valid";
    case ESP_OTA_IMG_INVALID: return "invalid";
    case ESP_OTA_IMG_ABORTED: return "aborted";
    default: return "undefined";
    }
}

// Copy s into out (n bytes), spaces and '/' replaced, so the field stays one token.
static void hil_slot_token(const char *s, char *out, size_t n)
{
    size_t i = 0;
    for (; s && s[i] && i < n - 1; i++)
        out[i] = (s[i] == ' ' || s[i] == '/' || s[i] == '=' || (unsigned char)s[i] < 0x21) ? '_' : s[i];
    out[i] = '\0';
    if (!i && n > 1) { out[0] = '-'; out[1] = '\0'; }
}

static void hil_slots_describe(char *buf, size_t len)
{
    size_t used = 0;
    int w = snprintf(buf, len, "running=%d boot=%d count=%d", hil_slot_index(esp_ota_get_running_partition()),
                     hil_slot_index(esp_ota_get_boot_partition()), hil_slot_count());
    if (w > 0) used = (size_t)w < len ? (size_t)w : len - 1;
    for (int i = 0; used < len - 1; i++)
    {
        const esp_partition_t *p = hil_slot_partition(i);
        if (!p) break;
        esp_app_desc_t d;
        uint8_t sha[32];
        if (esp_ota_get_partition_description(p, &d) != ESP_OK || esp_partition_get_sha256(p, sha) != ESP_OK)
        {
            w = snprintf(buf + used, len - used, " %d=empty", i);
        }
        else
        {
            char hex[65], proj[33], ver[33];
            for (int k = 0; k < 32; k++) snprintf(hex + 2 * k, 3, "%02x", sha[k]);
            hil_slot_token(d.project_name, proj, sizeof(proj));
            hil_slot_token(d.version, ver, sizeof(ver));
            w = snprintf(buf + used, len - used, " %d=%s/%s/%s/%s", i, hex, hil_slot_state_name(p), proj, ver);
        }
        if (w <= 0) break;
        used += (size_t)w < len - used ? (size_t)w : len - used - 1;
    }
}

// NULL on success, else a short reason (no spaces) for the error reply.
static const char *hil_slots_set_boot(int idx, bool wipe)
{
    const esp_partition_t *p = hil_slot_partition(idx);
    if (!p) return "no_such_slot";
    esp_app_desc_t d;
    if (esp_ota_get_partition_description(p, &d) != ESP_OK) return "slot_empty";
    // Verify the whole image first (esp_partition_get_sha256 runs the same esp_image_verify as
    // esp_ota_set_boot_partition), then wipe, then switch: any error leaves both the bonds and the boot slot alone.
    uint8_t sha[32];
    if (esp_partition_get_sha256(p, sha) != ESP_OK) return "image_invalid";
    if (wipe)
    {
        const esp_partition_t *nvs =
            esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_NVS, "nvs");
        if (!nvs || esp_partition_erase_range(nvs, 0, nvs->size) != ESP_OK) return "nvs_erase_failed";
    }
    if (esp_ota_set_boot_partition(p) != ESP_OK) return "image_invalid";
    return NULL;
}

static void hil_slots_restart(void)
{
    fflush(stdout);
    esp_restart();
}
