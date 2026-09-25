# Fork regression tests

These tests use real Home Assistant update entities and the `update.install` and
`update.skip` services. Network downloads are replaced with controlled operations;
the rollback test exercises HACS's actual backup implementation. No live Home
Assistant configuration or GitHub credentials are needed.

For the minimum supported runtime (Python 3.13):

```sh
scripts/install/pip_packages --requirement requirements_core_min.txt --requirement requirements_base.txt
scripts/install/frontend
python -m unittest discover -s tests_fork -v
```

For current Home Assistant, use a separate Python 3.14 environment and replace
`--requirement requirements_core_min.txt` with `homeassistant`.

The release workflow runs both environments before pushing an upstream merge or
publishing a release. This directory stays separate from upstream's test fixtures,
which assume HACS is registered as `hacs/integration`.

The controller in `custom_components/hacs/auto_update.py` owns unattended work.
Update entities register with it and request work on coordinator notifications.
It rechecks eligibility before each normal update service call. Entity recreation
pauses and drains it; integration unload stops it. Keep repository downloading,
compatibility checks, installation, and backup behavior in upstream's installer.
