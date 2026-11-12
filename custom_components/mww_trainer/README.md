# microWakeWord Trainer (Home Assistant custom integration)

Experimental integration for a local [TaterTotterson microWakeWord Trainer](https://github.com/TaterTotterson/microWakeWord-Trainer-Nvidia-Docker).

Provides a native **Wake word model** update entity and **Automatic model updates** switch on a dedicated **microWakeWord Trainer** device. It checks the selected model's SHA-256 every five minutes and confirms installation with the ESPHome success counter and status entity before recording the deployed version.

## Installation

Copy `custom_components/mww_trainer` to `/config/custom_components/mww_trainer` in Home Assistant and restart HA. Flash the matching ESPHome package on the Voice PE, then add **microWakeWord Trainer** under **Settings → Devices & services → Add integration**.

Configure the trainer URL, trained model **key** (not display label), and corresponding entities from the Voice PE:
- **Update model button**
- **Update status sensor**
- **Successful update count sensor**
- **Trainer Active Wake Words text entity**
- **Trainer Model Manifest URL text entity**

The integration reads `/api/trained_wake_words/catalog` and `/api/auto_train`. The auto-training `wake_phrase` is the primary training target. Additional training targets can be configured as comma-separated phrases for a second simultaneous wake word. Only explicitly selected phrases are uploaded. This is separate from the firmware's ordinary wake-word activation: auxiliary words such as Stop keep working and are excluded from uploads.

**Important:** The trainer currently exposes a single auto-training wake phrase, not the Voice PE's exact HA-selected two-word configuration. Therefore, explicitly configure any second training phrase; the integration cannot safely infer it from the trainer. Names must match the model's reported wake-word label.

**Manual mode (default):** Install from the native update entity. **Automatic mode:** Enable the switch to deploy the next detected model revision without approval. A successful deployment is only persisted after the update count increases and status reads `success`. On first installation, the integration does not assume that an existing model is already deployed.

Keep your previous automation disabled rather than deleting it until the new workflow is verified. This has not yet been tested against your live HA instance, trainer or device.
