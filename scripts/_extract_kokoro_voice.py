"""One-off, dependency-free extraction of a PyTorch-serialized Kokoro voice
tensor into a plain numpy array - deliberately avoids adding a torch
dependency to the project just to read two small tensors.

A ``.pt`` file saved by ``torch.save`` is a zip archive containing a pickled
"persistent id" reference to the raw storage bytes plus a small pickle
program describing dtype/shape/stride. This script implements the minimum
unpickler needed to resolve that reference against the real storage bytes
in the archive - it does not touch or shell out to torch.
"""

from __future__ import annotations

import pickle
import sys
import zipfile

import numpy as np

_DTYPES = {
    "FloatStorage": np.float32,
    "DoubleStorage": np.float64,
    "HalfStorage": np.float16,
    "LongStorage": np.int64,
    "IntStorage": np.int32,
}


class _StorageRef:
    def __init__(self, storage_type: str, key: str, numel: int) -> None:
        self.storage_type = storage_type
        self.key = key
        self.numel = numel


class _TorchFreeUnpickler(pickle.Unpickler):
    def persistent_load(self, pid):
        assert pid[0] == "storage"
        _, storage_type, key, _location, numel = pid
        return _StorageRef(storage_type.__name__, key, numel)

    def find_class(self, module, name):
        if module == "torch._utils" and name == "_rebuild_tensor_v2":
            def _rebuild(storage, storage_offset, size, stride, requires_grad, backward_hooks, *_rest):
                return {"storage": storage, "offset": storage_offset, "size": size}
            return _rebuild
        if module == "torch" and name in _DTYPES:
            return type(name, (), {"__name__": name})
        if module == "collections" and name == "OrderedDict":
            return dict
        return super().find_class(module, name)


def load_voice_style(pt_path: str) -> np.ndarray:
    with zipfile.ZipFile(pt_path) as archive:
        root = archive.namelist()[0].split("/")[0]
        with archive.open(f"{root}/data.pkl") as fh:
            obj = _TorchFreeUnpickler(fh).load()
        ref = obj["storage"]
        raw = archive.read(f"{root}/data/{ref.key}")
        dtype = _DTYPES[ref.storage_type]
        flat = np.frombuffer(raw, dtype=dtype, count=ref.numel)
        return flat.reshape(obj["size"]).copy()


if __name__ == "__main__":
    src, dst_key, out_npz = sys.argv[1], sys.argv[2], sys.argv[3]
    array = load_voice_style(src)
    print(dst_key, array.shape, array.dtype)
    try:
        existing = dict(np.load(out_npz))
    except FileNotFoundError:
        existing = {}
    existing[dst_key] = array.astype(np.float32)
    np.savez(out_npz, **existing)
