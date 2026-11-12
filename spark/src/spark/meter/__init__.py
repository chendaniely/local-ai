"""The usage meter's first numbers (website/design/usage-meter-now.md): `spark usage`, a cost summary at Claude's
prices. This package holds the price files (mapping.yaml, prices.yaml, providers.yaml) and what reads them.

The front never imports it, so a change to these files restarts only the gate and the brake. At module level it
imports only the standard library, `yaml` and `spark.paths`, since `build_parser` imports every command's module."""
