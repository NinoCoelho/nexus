import { useState, useEffect } from "react";
import {
  listDatabases,
  listDatabaseTables,
  getVaultDataTable,
  type DatabaseSummary,
  type DatabaseTableSummary,
} from "../../../api/datatable";
import TemplateInput from "../TemplateInput";
import { TABLE_ACTIONS } from "./constants";
import type { StepFormProps } from "./shared";

export default function TableActionForm({
  step,
  onChangeStep,
  stepRefs,
  stepSchemas,
}: StepFormProps) {
  const [appDatabases, setAppDatabases] = useState<DatabaseSummary[]>([]);
  const [appTables, setAppTables] = useState<DatabaseTableSummary[]>([]);
  const [tableFields, setTableFields] = useState<
    { name: string; kind: string }[]
  >([]);

  useEffect(() => {
    listDatabases()
      .then((res) => setAppDatabases(res.databases))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (step.table_path) {
      getVaultDataTable(step.table_path)
        .then((t) => {
          setTableFields(
            (t.schema?.fields || []).map((f: any) => ({
              name: f.name,
              kind: f.kind,
            })),
          );
        })
        .catch(() => setTableFields([]));
    }
  }, [step.table_path]);

  const handleDatabaseSelect = (folder: string) => {
    listDatabaseTables(folder)
      .then((res) => setAppTables(res.tables))
      .catch(() => setAppTables([]));
  };

  return (
    <>
      <div className="wf-field">
        <label>Action</label>
        <select
          value={step.action || ""}
          onChange={(e) => onChangeStep({ action: e.target.value })}
        >
          <option value="">— select action —</option>
          {TABLE_ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
          ))}
        </select>
      </div>

      <div className="wf-field">
        <label>App</label>
        <select
          value=""
          onChange={(e) => {
            if (e.target.value) handleDatabaseSelect(e.target.value);
          }}
        >
          <option value="">— select app —</option>
          {appDatabases.map((d) => (
            <option key={d.folder} value={d.folder}>
              {d.title || d.folder}
            </option>
          ))}
        </select>
      </div>

      <div className="wf-field">
        <label>Table</label>
        <select
          value={step.table_path || ""}
          onChange={(e) => onChangeStep({ table_path: e.target.value })}
        >
          <option value="">— select table —</option>
          {appTables.map((t) => (
            <option key={t.path} value={t.path}>
              {t.title || t.path}
            </option>
          ))}
        </select>
      </div>

      {step.table_path &&
        (step.action === "add_row" || step.action === "update_row") &&
        tableFields.length > 0 && (
          <div className="wf-field">
            <label>Row Data</label>
            <TemplateInput
              value={
                step.row_data
                  ? JSON.stringify(step.row_data, null, 2)
                  : JSON.stringify(
                      Object.fromEntries(
                        tableFields.map((f) => [f.name, ""]),
                      ),
                      null,
                      2,
                    )
              }
              onChange={(val) => {
                try {
                  onChangeStep({ row_data: JSON.parse(val) });
                } catch {}
              }}
              steps={stepRefs}
              stepSchemas={stepSchemas}
              multiline
              minLines={4}
              placeholder='{"field": "{{steps.prev.result}}"}'
            />
          </div>
        )}

      {step.table_path && step.action === "update_row" && (
        <div className="wf-field">
          <label>Row ID</label>
          <TemplateInput
            value={step.row_id || ""}
            onChange={(val) => onChangeStep({ row_id: val })}
            steps={stepRefs}
            stepSchemas={stepSchemas}
            placeholder="{{steps.prev.output._id}}"
          />
        </div>
      )}

      {step.table_path && step.action === "find_rows" && (
        <div className="wf-field">
          <label>Where (JSON)</label>
          <TemplateInput
            value={step.where ? JSON.stringify(step.where, null, 2) : "{}"}
            onChange={(val) => {
              try {
                onChangeStep({ where: JSON.parse(val) });
              } catch {}
            }}
            steps={stepRefs}
            stepSchemas={stepSchemas}
            multiline
            minLines={3}
            placeholder='{"status": "open"}'
          />
        </div>
      )}
    </>
  );
}
