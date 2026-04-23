# dawnpy-ble

BLE transport extension for `dawnpy`.

Main Dawn project: [railab/dawn](https://github.com/railab/dawn).

The package exposes the standalone `dawnpy-ble` command. It does not add
commands to `dawnpy`.

QA follows the shared Python tool baseline:

```sh
tox
tox -e py
tox -e format
tox -e flake8
tox -e type
```
