# AGENTS.md

Guidelines for contributing to **ha-xtool**, a local-only Home Assistant
integration for xTool lasers, welders and printers (domain `xtool`,
Home Assistant ≥ 2025.10). Read this before opening a pull request; it
applies to people and coding agents alike.

## Layout

```
custom_components/xtool/
├── __init__.py, config_flow.py, coordinator.py, entity.py, const.py
├── <platform>.py          one per HA platform; only calls coordinator.build_<platform>()
├── services.py/.yaml      the xtool.* actions
├── translations/          en.json and de.json (no strings.json)
└── protocols/
    ├── base.py            XtoolDeviceModel (capability flags), XtoolDeviceState, XtoolProtocol
    ├── accessories/       Bluetooth/serial accessories (one definition per accessory)
    └── s1/ d_series/ rest/ ws_v2/   one directory per protocol family:
        protocol.py        wire transport and parsing
        coordinator.py     polling and events
        entities.py        the family's entities and build_<family>_<platform>()
        models.py          one XtoolDeviceModel per supported device
docs/PROTOCOL.md           wire-level reference
docs/P2.md                 P2 camera details for users (linked from the README)
tests/                     feature tests
tests/core/                shared behaviour: config flow, entry life, model detection
tests/models/              one module per model confirmed on hardware
```

## Code

- **Model differences go through capability flags or protocol
  subclasses.** Flags (`has_*`) default to `False`; a model opts in
  in `models.py`. Never branch on `model_id` in shared code. The REST
  and WS-V2 entries of the same device carry the same flags, unless a
  flag gates a request only one firmware has.
- **New entities go in the family's `entities.py`.** Platform files stay
  minimal. The only exceptions are entities every model has: Status
  (`sensor.py`) and those of a linked Power switch (`switch.py`,
  `number.py`).
- **Follow the device's own client.** Requests, payloads and their
  meaning come from what xTool Studio (or the firmware) does; name the
  source in the pull request instead of guessing.
- **IDs:** always call `_set_unique_id("<key>")`. It sets the unique ID
  (`<serial>_<key>`) and the entity ID
  (`<domain>.xtool_<model>_<serial>_<key>`). Never set either yourself,
  and never change the key of an existing entity.
- **Names come from translations** (`translation_key`). Every new name,
  state, event type and error is added to `en.json` **and** `de.json`.
- **No speculative code:** no error handling, fallbacks or migrations
  for cases that cannot happen (e.g. renaming entities that were never
  released). Validate at the device and user boundaries only.
- **Comments explain why**, not what. Set up instance attributes in
  `__init__`, not with `getattr(self, "_x", default)`.
- Do not use deprecated Home Assistant APIs (for example
  `device_registry.async_get_device` or `registry.devices` as a
  mapping); a test guards against them.
- Never leave temporary debug or diagnostic code in a pull request.

## Documentation

- `docs/PROTOCOL.md` describes the **device**, as if for someone
  writing a new client: endpoints, M-codes, payloads, pushes, firmware
  quirks. Never mention "the integration", Home Assistant, entities,
  source symbols or pull requests there. Add every new endpoint or push
  you use.
- `README.md` is for users: entities, options, supported devices,
  behaviour. Update it with every user-visible change. No internals.

## Tests

Python ≥ 3.14.2:

```bash
pip install -r requirements_test.txt
pytest -q
```

CI runs the whole suite on every push and pull request; it must pass.

- **Every change comes with its tests** in the same pull request: a
  fix with a test that fails without it.
- **Tests always run.** Never skip a test by checking whether a feature
  is in the source; delete the test of behaviour you remove.
- **Models confirmed on hardware have their own tests** in
  `tests/models/test_<model>.py`, built from real device replies
  (`<model>_capture.py`, serial number replaced). Models share a lot of
  code: if your change touches a family or shared code, the tests of
  every confirmed model must still pass, and you extend them where your
  change reaches them. When a user confirms a model on hardware, add
  its tests.
- To capture replies: set the `xtool` log level to DEBUG, reload the
  entry, copy the `WS <command> -> <reply>` lines (S1) or the request
  and push lines (other families) from the log, set the level back.
- Tests never reach the network. `tests/conftest.py` blocks xTool's
  cloud and discovery and provides `build_coordinator(hass, model)`;
  `v2_device` / `v2_protocol` give a fake WS-V2 device
  (`tests/fake_ws_v2.py`).

## Pull requests

- **One topic per pull request**, based on the latest `main`. Rebase
  instead of merging `main` in. If it needs another open pull request,
  say so at the top.
- The description says **why**, **what changed** and **how it was
  tested**: which model and firmware it ran on, and what is covered by
  tests only.
- Call out anything that changes existing behaviour (entities removed or
  renamed, states that change meaning).
- Commit messages in English, imperative, one logical change per commit.
- Do not change `manifest.json`'s version; the maintainer sets it for a
  release.
