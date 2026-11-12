# microWakeWord Trainer (Home Assistant integration)

Experimental integration for the [TaterTotterson microWakeWord Trainer](https://github.com/TaterTotterson/microWakeWord-Trainer-Nvidia-Docker). Install through Home Assistant Marketplace / HACS using the repository URL `https://github.com/websterwh/microWakeWords`, or place the `custom_components/mww_trainer` folder in Home Assistant's config directory and restart Home Assistant.

## Setup

In **Settings → Devices & services → Add integration → microWakeWord Trainer**, choose:

1. **Trainer URL** (the address Home Assistant can reach).
2. **Wake word / model name** (trainer catalog key or display name, such as Alexa).
3. **Wake-word devices** (select one or multiple ESPHome trainer devices, EchoLocal or techo5 satellites).
4. Optional additional training target phrases and automatic update mode.

You do **not** need to select individual entity IDs. For direct ESPHome devices the integration discovers the update button, update status, update counter, model manifest text field, and active-target text field from the chosen device. Compatible devices are detected by available features, not by Voice PE model name.

## Supported backends

| Backend | Publish/update | Confirmation | Captured audio |
| --- | --- | --- | --- |
| ESPHome with this repository's `voice_pe_trainer.yaml` package | Install model into device's A/B runtime slots, no firmware reflash | Device's success status **and** increased update counter | Wake detections and close misses for explicitly configured training targets only |
| [EchoLocal](https://github.com/ygelfand/echolocal) | Publish model into Home Assistant's `custom_wake_words` directory | **Publication only**; not device installation | Not yet integrated with the TaterTotterson trainer |
| [techo5](https://github.com/HuskerMinion/techo5) | Publish model into Home Assistant's `custom_wake_words` directory | **Publication only**; not device installation | Not yet integrated with the TaterTotterson trainer |

**For EchoLocal and techo5:** After publication, **restart Home Assistant** to load the custom wake-word catalog, and select the wake word in the Assist satellite's settings. Both systems support fetching Home Assistant-offered custom wake-word models. Publishing a model is **not** a claim that the satellite has installed or activated it. No firmware reflash is necessary on these devices for this catalog route. techo5 also has a direct upload option in its own authenticated setup page, but the integration deliberately does not bypass that security flow.

**Note:** techo5 documents a difference in wake-score behavior for models uploaded through its setup page versus Home Assistant. If a catalog-installed model is insensitive, follow techo5's own upload instructions.

## Updates and capture safety

The integration compares trained model SHA-256 checksums every five minutes. In manual mode, install from the **Wake word model** update entity. In automatic mode the switch automatically installs to a direct ESPHome device, or **publishes** a new model to the HA custom-wake-word catalog for EchoLocal/techo5. Publishing does not restart HA or silently change the satellite's selected model.

For ESPHome capture, the trainer's `/api/auto_train` primary wake phrase plus explicitly listed optional additional phrases forms the allowlist. This prevents unrelated compiled wake words, including auxiliary words such as Stop, from being uploaded simply because they are loaded. Normal detection, timers, and voice pipelines are unchanged. This is not automatic discovery of both Home Assistant-selected assistant wake-word slots: any second training-target phrase must currently be entered explicitly.

Keep the old hash automation disabled while testing, and turn on automatic updates only after a successful manual test.

## Version 0.3.0 migration and trainer errors

After installing the Marketplace update, restart Home Assistant to reload the configuration-flow labels from `strings.json`. The setup and options flow now lets you select **multiple devices**. For an existing entry, use **Configure** to revise its selected devices. Each device has a separate native update entity; the automatic-updates switch applies to all devices in the trainer/model entry.

A trainer-side HTTP 500 on `/api/trained_wake_words/<model>.esphome.json` no longer prevents initial integration setup. The integration continues syncing training targets and tries the canonical `.wake-bundle.json` for catalog metadata. **Direct ESPHome model updates still require a working manifest endpoint**; the update entity reports its error and does not mark an installation successful. Check the trainer container's server traceback and repair/restart the trainer when that endpoint returns 500. Firmware does not need reflashing for these Home Assistant integration changes.

## Version 0.4.0: verified model versions and managed URLs

For direct ESPHome satellites, **Trainer Model Manifest URL**, **Trainer App URL** and **Trainer Active Wake Words** are synchronized by the Home Assistant integration. Do not edit those fields manually. An unavailable trainer manifest endpoint does **not** prevent its URL from being populated; direct installation still requires the trainer to serve a valid manifest.

The updated ESPHome package publishes a **Runtime Model SHA256** diagnostic sensor, which hashes the actual validated .tflite bytes active in the runtime slot, including models restored from flash after reboot. HA compares that digest to the trainer's model checksum. When a device has no readable fingerprint (older firmware, offline, or compiled-only fallback), its installed version is *unknown*, and no unverified update notification is shown. Automatic direct deployments wait for an installed hash. Model changes update HA immediately, without depending on integration deployment history.

**One ESPHome reflash is necessary to add the hash sensor and correct the runtime update button.** Afterwards install v0.4.0 through the Home Assistant Marketplace, restart Home Assistant, and verify that the runtime hash sensor is visible and the trainer URL entities are populated. The old update automation should remain disabled.
