> **Home Assistant 2026.11 Marketplace integration:** In **Settings → Marketplace → Add from link**, enter `https://github.com/websterwh/microWakeWords`, then install **microWakeWord Trainer**. After installation, configure it under **Settings → Devices & services → Add integration**. Select the trainer URL, **Wake word / model name**, and one supported device. It supports direct updates on ESPHome devices using this package, plus Home Assistant custom wake-word catalog publication for [EchoLocal](https://github.com/ygelfand/echolocal) and [techo5](https://github.com/HuskerMinion/techo5) (restart Home Assistant and select the model afterward). ESPHome firmware package: `github://websterwh/microWakeWords/esphome/voice_pe_trainer.yaml@main`. See the [integration setup guide](custom_components/mww_trainer/README.md). This integration is experimental and should be tested before enabling automatic deployments.

<div align="center">
  <a href="https://taterassistant.com">
    <img src="images/tater-repo-logo.png" alt="microWakeWords" width="460"/>
  </a>
</div>
<h3 align="center">
  <a href="https://taterassistant.com">taterassistant.com</a>
</h3>

---

## Repository Retired

This repository is no longer the active firmware home for Tater satellites.

Tater has moved away from ESPHome-based firmware and now uses native firmware for supported satellite hardware such as VoicePE, Sat1, ReSpeaker devices, and S3 Box display satellites.

**[Tater Native Firmware](https://github.com/TaterTotterson/Tater-Native-Firmware)**

## Wake Word Requests

New wake-word requests now live in the Tater Wake Words repo:

**[Tater Wake Words](https://github.com/TaterTotterson/Tater-Wake-Words)**

To request a new word, open an issue there with a title like:

```text
mww: hey tater
```

That repo handles new generated wake words and publishes the updated wake-word catalog for Tater Native Firmware.

## Home Assistant Users

This retired ESPHome firmware is no longer the Home Assistant path for these satellites. Flash Tater Native Firmware, then choose either of the supported connection options:

- **Home Assistant:** Use the [Tater Satellite integration](https://github.com/TaterTotterson/Tater-Home-Assistant-Satellites) to connect and manage Tater Native satellites directly in Home Assistant.
- **Tater:** Use [Tater](https://github.com/TaterTotterson/Tater) for the complete Tater Assistant voice experience.

- **Tater Native Firmware:** [https://github.com/TaterTotterson/Tater-Native-Firmware](https://github.com/TaterTotterson/Tater-Native-Firmware)
- **Tater Wake Words:** [https://github.com/TaterTotterson/Tater-Wake-Words](https://github.com/TaterTotterson/Tater-Wake-Words)

## Status

Retired. No new wake-word requests, ESPHome YAML updates, or Home Assistant satellite firmware changes are planned here.
