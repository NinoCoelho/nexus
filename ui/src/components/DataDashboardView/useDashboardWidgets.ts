import { useCallback, useState } from "react";
import {
  addWidget,
  deleteWidget,
  designWidget,
  type Dashboard,
  type DashboardWidget,
} from "../../api/dashboard";
import type { ToastAPI } from "../../toast/ToastProvider";

interface UseDashboardWidgetsParams {
  folder: string;
  setDashboard: React.Dispatch<React.SetStateAction<Dashboard | null>>;
  reload: () => Promise<void>;
  toast: ToastAPI;
}

export function useDashboardWidgets({
  folder,
  setDashboard,
  reload,
  toast,
}: UseDashboardWidgetsParams) {
  const [showWidgetWizard, setShowWidgetWizard] = useState(false);
  const [editingWidget, setEditingWidget] = useState<DashboardWidget | null>(null);
  const [sqlEditWidget, setSqlEditWidget] = useState<DashboardWidget | null>(null);
  const [aiFixContext, setAiFixContext] = useState<{ widget: DashboardWidget; error: string } | null>(null);
  const [pendingWidgetRemoval, setPendingWidgetRemoval] = useState<DashboardWidget | null>(null);

  const handleEditWidget = useCallback(async (widget: DashboardWidget) => {
    try {
      const next = await addWidget(folder, widget);
      setDashboard(next);
      setShowWidgetWizard(false);
      setEditingWidget(null);
      toast.success(`Saved "${widget.title}"`);
    } catch (e) {
      toast.error("Couldn't save widget", { detail: (e as Error).message });
    }
  }, [folder, toast, setDashboard]);

  const handleDesignWidget = useCallback((widget: DashboardWidget) => {
    const goal = widget.prompt || `Redesign widget "${widget.title}" with a better query and visualization`;
    void (async () => {
      try {
        const { session_id: _sid } = await designWidget(folder, widget.id, goal);
        void _sid;
        toast.info(`Designing "${widget.title}"\u2026`, { detail: "The agent is inspecting your schema and planning a query." });
      } catch (e) {
        toast.error("Couldn't start design", { detail: (e as Error).message });
      }
    })();
  }, [folder, toast]);

  const handleSqlEditSave = useCallback(async (widget: DashboardWidget) => {
    try {
      const next = await addWidget(folder, widget);
      setDashboard(next);
      setSqlEditWidget(null);
      toast.success(`Saved "${widget.title}"`);
    } catch (e) {
      toast.error("Couldn't save widget", { detail: (e as Error).message });
    }
  }, [folder, toast, setDashboard]);

  const handleResizeWidget = useCallback(async (widget: DashboardWidget, size: "sm" | "md" | "lg") => {
    if (widget.size === size) return;
    setDashboard((d) =>
      d
        ? {
            ...d,
            widgets: (d.widgets ?? []).map((w) => (w.id === widget.id ? { ...w, size } : w)),
          }
        : d,
    );
    try {
      const next = await addWidget(folder, { ...widget, size });
      setDashboard(next);
    } catch (e) {
      toast.error("Couldn't resize widget", { detail: (e as Error).message });
      void reload();
    }
  }, [folder, reload, toast, setDashboard]);

  const handleRemoveWidget = useCallback(async (widgetId: string) => {
    try {
      const next = await deleteWidget(folder, widgetId);
      setDashboard(next);
    } catch (e) {
      toast.error("Couldn't remove widget", { detail: (e as Error).message });
    }
  }, [folder, toast, setDashboard]);

  return {
    showWidgetWizard,
    setShowWidgetWizard,
    editingWidget,
    setEditingWidget,
    sqlEditWidget,
    setSqlEditWidget,
    aiFixContext,
    setAiFixContext,
    pendingWidgetRemoval,
    setPendingWidgetRemoval,
    handleEditWidget,
    handleDesignWidget,
    handleSqlEditSave,
    handleResizeWidget,
    handleRemoveWidget,
  };
}
