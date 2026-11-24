"""
Renovation plan WRITE surface and façade.

Cell mutations, row lifecycle, formula recomputation and submit/approve stay
here (Phase 4 decomposition); the READ surface lives in ``plan_reads`` and the
shared primitives in ``service_common``. The public names below are re-exported
from those modules so ``from src.renovation.service import X`` keeps working.
"""

# --- Façade: keep the historical `from src.renovation.service import X` contract ---
from src.raw_materials.service import CellValidationError  # noqa: F401
from src.renovation.cell_mutations import (  # noqa: F401
    add_plan_row,
    apply_renovation_cell_value,
    assign_plan_row_asset,
    delete_plan_row,
    update_plan_cells,
)
from src.renovation.errors import RenovationPlanNotFoundError  # noqa: F401
from src.renovation.lifecycle import (  # noqa: F401
    approve_plan,
    submit_plan,
)
from src.renovation.plan_reads import (  # noqa: F401
    RenovationPlanExportData,
    iter_plan_export_rows,
    read_meta,
    read_plan,
    read_plan_diff,
    read_plan_export_data,
    read_plan_export_meta,
    read_plan_facets,
    read_plan_rows,
    read_plan_summary,
    read_plan_versions,
    resolve_renovation_access,
)
from src.renovation.recompute import (  # noqa: F401
    _recalculate_row,
    recompute_plan_rows,
)
from src.renovation.service_common import (  # noqa: F401
    NoRenovationAccessError,
    RenovationPermissionError,
    RenovationPlanStateError,
)
