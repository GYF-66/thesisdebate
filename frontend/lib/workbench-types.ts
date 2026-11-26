import type { JSONContent } from "@tiptap/react";

export type DocumentSession = {
  id: string;
  name: string;
  type: "docx" | "pdf" | "markdown";
  ready: boolean;
  content: JSONContent | string;
  changes: Change[];
  activeChangeId: string;
  runId: string | null;
  preview: PreviewResponse | null;
  uploadMessage: string;
  latex: string;
};

export type Status = "pending" | "accepted" | "rejected";
export type ChangeKind = "format" | "structure" | "reference" | "ai";
export type Change = {
  id: string;
  title: string;
  detail: string;
  before: string;
  after: string;
  kind: ChangeKind;
  status: Status;
  anchor: string;
  rule: string;
  anchorStart?: number;
  anchorEnd?: number;
  nodeId?: string;
  contextHash?: string;
};
export type PreviewResponse = {
  preview?: string;
  source?: string;
  notes?: string[];
  applied_count?: number;
};
export type WorkbenchView = "workspace" | "comparison" | "review";
export const changeKinds: { key: ChangeKind; label: string; color: string }[] =
  [
    { key: "format", label: "格式规范", color: "#b9a0f5" },
    { key: "structure", label: "结构层次", color: "#f5d888" },
    { key: "reference", label: "参考引用", color: "#8ed8c6" },
    { key: "ai", label: "AI 建议", color: "#efa6b5" },
  ];
export function kindName(kind: ChangeKind) {
  return changeKinds.find((item) => item.key === kind)?.label || kind;
}
