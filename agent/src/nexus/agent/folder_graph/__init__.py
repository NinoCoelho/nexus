"""Per-folder ontology-isolated knowledge graphs.

Each folder gets its own ``.nexus-graph/`` directory holding the loom
``GraphRAGEngine`` databases, a manifest tracking file mtimes/hashes, and
a JSON snapshot of the ontology. Index data travels with the folder.
"""

from __future__ import annotations

from ._engine_pool import (
    close_all_engines as close_all_engines,
    delete_folder_index as delete_folder_index,
    open_folder_engine as open_folder_engine,
)
from ._indexer import (
    index_folder_streaming as index_folder_streaming,
    list_indexable_files as list_indexable_files,
)
from ._queries import (
    full_subgraph as full_subgraph,
    query as query,
    subgraph_for_seed as subgraph_for_seed,
)
from ._stale import stale_files as stale_files
from ._storage import (
    folder_dot_dir as folder_dot_dir,
    is_initialized as is_initialized,
    load_meta as load_meta,
    normalize_folder as normalize_folder,
    save_meta as save_meta,
)
from ._tabs_state import (
    add_tab as add_tab,
    list_tabs as list_tabs,
    remove_tab as remove_tab,
    set_tabs as set_tabs,
)
from ._wizard import (
    answer_wizard as answer_wizard,
    start_wizard as start_wizard,
)

__all__ = [
    "add_tab",
    "answer_wizard",
    "close_all_engines",
    "delete_folder_index",
    "folder_dot_dir",
    "full_subgraph",
    "index_folder_streaming",
    "is_initialized",
    "list_indexable_files",
    "list_tabs",
    "load_meta",
    "normalize_folder",
    "open_folder_engine",
    "query",
    "remove_tab",
    "save_meta",
    "set_tabs",
    "stale_files",
    "start_wizard",
    "subgraph_for_seed",
]
