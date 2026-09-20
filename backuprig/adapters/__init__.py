from .base import BackupResult, ConnectionParams, VendorAdapter
from .cisco_ios import CiscoIOSAdapter
from .esxi import ESXiAdapter
from .generic_ssh import GenericSSHAdapter
from .kerio import KerioControlAdapter
from .mikrotik import MikroTikAdapter

ADAPTERS = {
    cls.key: cls
    for cls in (
        CiscoIOSAdapter,
        MikroTikAdapter,
        KerioControlAdapter,
        ESXiAdapter,
        GenericSSHAdapter,
    )
}


def get_adapter(key: str) -> VendorAdapter:
    try:
        cls = ADAPTERS[key]
    except KeyError:
        raise ValueError(f"unknown vendor adapter: {key!r} (known: {sorted(ADAPTERS)})")
    return cls()


def list_adapters():
    """Return [(key, label), ...] sorted by label, for populating a UI dropdown."""
    return sorted(((k, cls.label) for k, cls in ADAPTERS.items()), key=lambda t: t[1])


__all__ = [
    "BackupResult", "ConnectionParams", "VendorAdapter",
    "CiscoIOSAdapter", "MikroTikAdapter", "KerioControlAdapter", "ESXiAdapter",
    "GenericSSHAdapter", "ADAPTERS", "get_adapter", "list_adapters",
]
