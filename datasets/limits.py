from dataclasses import dataclass


class ValidationError(Exception):
    """Safe stable code; no untrusted content or filesystem path in messages."""


@dataclass(frozen=True)
class Limits:
    # Provisional admission limits, NOT measured production capacity.
    archive_bytes: int = 30 * 1024**3
    expanded_bytes: int = 250 * 1024**3
    entry_bytes: int = 512 * 1024**2
    entries: int = 100000
    directory_bytes: int = 32 * 1024**2
    ratio: int = 200
    name_bytes: int = 4096
    xml_bytes: int = 32 * 1024**2
    xml_depth: int = 64
    xml_attributes: int = 32
    xml_attribute_bytes: int = 16384
    xml_text_bytes: int = 65536
    xml_nodes: int = 1000000
    annotation_images: int = 100000
    boxes: int = 250000
    tags: int = 250000
    image_bytes: int = 50 * 1024**2
    image_pixels: int = 16000000
    image_dimension: int = 32768
    index_bytes: int = 256 * 1024**2
    workspace_bytes: int = 1024**3
    worker_memory_bytes: int = 1024**3
    timeout_seconds: float = 3600
    decode_seconds: float = 60
    block_bytes: int = 65536

    def __post_init__(self):
        if any(value <= 0 for value in self.__dict__.values()):
            raise ValueError("Validation limits must be positive")
        if self.block_bytes > 1024**2:
            raise ValueError("Maximum I/O block is 1 MiB")
        if self.workspace_bytes < 2*self.index_bytes + max(self.xml_bytes, self.image_bytes) + 1024**2:
            raise ValueError("Workspace must cover index, journal, entry spool and manifest")
