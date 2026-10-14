"""Transport policy over the one authoritative Store; no in-memory trust fallback."""

from dataclasses import dataclass
from typing import Any

from metamuse.models import CoreError, ErrorCode
from metamuse.store import MuseTrustSnapshot, Store


@dataclass(frozen=True)
class SelectedVM:
    vm_id: str
    name: str
    endpoint_url: str
    vm_type: str
    vm_state: str
    replacement: bool
    confidential_hint: bool = False


def storage_stop() -> CoreError:
    return CoreError(
        ErrorCode.STORAGE,
        "Muse security state is unavailable or stale",
        next_action="Restore the bound account security-state store before reconnecting",
    )


def protocol_stop() -> CoreError:
    return CoreError(
        ErrorCode.PROTOCOL_MISMATCH,
        "Muse standard verification refused this context",
        next_action=(
            "Stop for protocol review or use a supported verifier; do not weaken verification"
        ),
    )


@dataclass(frozen=True)
class StoreMuseTrust:
    store: Store
    provider: str
    account: str

    def freeze(self, auth: dict[str, Any], selected: SelectedVM) -> MuseTrustSnapshot:
        return self.store.muse_freeze(
            provider=self.provider,
            account=self.account,
            vm_id=selected.vm_id,
            viewer_id=auth["viewer_id"],
            session_binding_id=auth["session_binding_id"],
            confidential=selected.confidential_hint
            or selected.vm_type == "hatch_cvm"
            or selected.vm_state.startswith("CVM_"),
        )

    def assert_current(self, snapshot: MuseTrustSnapshot) -> None:
        self.store.muse_assert_current(snapshot)

    def confidential(self, snapshot: MuseTrustSnapshot) -> None:
        self.store.muse_record_confidential(snapshot)

    def standard_handoff(self, snapshot: MuseTrustSnapshot, handoff) -> None:
        self.store.muse_standard_handoff(snapshot, handoff)
