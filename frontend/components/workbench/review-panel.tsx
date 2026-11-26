"use client";

import {
  ArrowUpRight,
  Check,
  CheckCheck,
  CircleDot,
  Highlighter,
  MessageSquareText,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { changeKinds, kindName, type Change } from "@/lib/workbench-types";

type Agent = { name: string; tone: string; time: string; text: string };

export function ReviewPanel({
  changes,
  active,
  tab,
  agents,
  onTabChange,
  onSelect,
  onDecide,
  busy,
}: {
  busy: boolean;
  changes: Change[];
  active: Change | undefined;
  tab: "changes" | "debate";
  agents: Agent[];
  onTabChange: (tab: "changes" | "debate") => void;
  onSelect: (change: Change) => void;
  onDecide: (change: Change, decision: "accept" | "reject") => void;
}) {
  const pending = changes.filter((item) => item.status === "pending");
  let offset = 0;
  const slices = changeKinds.map((kind) => {
    const count = pending.filter((item) => item.kind === kind.key).length;
    const length = pending.length ? (count / pending.length) * 100 : 0;
    const slice = { ...kind, count, length, offset };
    offset += length;
    return slice;
  });
  return (
    <aside className="review-column" id="review" aria-label="评审面板">
      <Card className="distribution-card">
        <div className="card-heading">
          <h2>建议分布</h2>
          <Badge variant="secondary">当前待处理</Badge>
        </div>
        <div className="distribution-body">
          <div
            className="donut"
            role="img"
            aria-label={
              pending.length
                ? slices
                    .map((item) => `${item.label} ${item.count} 项`)
                    .join("，")
                : "没有待处理建议"
            }
          >
            <svg viewBox="0 0 120 120" aria-hidden="true">
              <circle
                className="donut-track"
                cx="60"
                cy="60"
                r="48"
                fill="none"
                strokeWidth="11"
              />
              {slices
                .filter((slice) => slice.count)
                .map((slice) => (
                  <circle
                    key={slice.key}
                    cx="60"
                    cy="60"
                    r="48"
                    fill="none"
                    stroke={slice.color}
                    strokeWidth="11"
                    pathLength="100"
                    strokeDasharray={`${Math.max(slice.length - 1.4, 0)} ${100 - Math.max(slice.length - 1.4, 0)}`}
                    strokeDashoffset={-slice.offset}
                    transform="rotate(-90 60 60)"
                    strokeLinecap="round"
                  />
                ))}
            </svg>
            <div className="donut-label">
              <small>待处理</small>
              <strong>
                {pending.length}
                <span>项</span>
              </strong>
            </div>
          </div>
          <div className="distribution-legend">
            {slices.map((slice) => (
              <div key={slice.key}>
                <i style={{ background: slice.color }} />
                <span>{slice.label}</span>
                <strong>{slice.count}</strong>
              </div>
            ))}
          </div>
        </div>
      </Card>
      <Card className="review-card">
        <Tabs
          value={tab}
          onValueChange={(value) => onTabChange(value as "changes" | "debate")}
        >
          <div className="review-card-header">
            <TabsList className="review-tabs" aria-label="评审内容">
              <TabsTrigger value="changes">
                修订建议 <span>{pending.length}</span>
              </TabsTrigger>
              <TabsTrigger value="debate">Agent 辩论</TabsTrigger>
            </TabsList>
          </div>
          <TabsContent value="changes" className="changes-panel">
            <div className="list-label">
              <span>逐项审阅 · 由你决定</span>
              <span>{String(changes.length).padStart(2, "0")} 条记录</span>
            </div>
            <div className="change-list">
              {pending.map((change, index) => (
                <button
                  type="button"
                  key={change.id}
                  className={
                    "change-card " +
                    (active?.id === change.id ? "selected" : "")
                  }
                  aria-pressed={active?.id === change.id}
                  onClick={() => onSelect(change)}
                >
                  <div className="change-card-top">
                    <Badge className={"kind-badge kind-" + change.kind}>
                      {kindName(change.kind)}
                    </Badge>
                    <span className="change-index">
                      {String(index + 1).padStart(2, "0")}
                      <ArrowUpRight size={14} />
                    </span>
                  </div>
                  <strong>{change.title}</strong>
                  <p>{change.detail}</p>
                  <span className="change-anchor">
                    <CircleDot size={11} />
                    {change.anchor}
                  </span>
                </button>
              ))}
              {!pending.length && (
                <div className="empty-state">
                  <CheckCheck size={28} />
                  <strong>这轮建议已处理完毕</strong>
                  <p>继续打磨正文，或运行一次新的扫描。</p>
                </div>
              )}
              {changes
                .filter((item) => item.status !== "pending")
                .map((change) => (
                  <button
                    type="button"
                    key={change.id}
                    className={"resolved-card " + change.status}
                    onClick={() => onSelect(change)}
                  >
                    {change.status === "accepted" ? (
                      <Check size={15} />
                    ) : (
                      <X size={15} />
                    )}
                    <span>{change.title}</span>
                    <small>
                      {change.status === "accepted" ? "已接受" : "已保留"}
                    </small>
                  </button>
                ))}
            </div>
            {active && (
              <section className="selected-detail" aria-label="当前修订">
                <div className="detail-heading">
                  <span>
                    <Highlighter size={14} />
                    当前修订
                  </span>
                  <Badge variant="outline">
                    {active.status === "pending"
                      ? "待确认"
                      : active.status === "accepted"
                        ? "已接受"
                        : "已保留"}
                  </Badge>
                </div>
                <strong>{active.title}</strong>
                <div className="change-diff">
                  <div className="before">
                    <span>原文</span>
                    <p>{active.before}</p>
                  </div>
                  <div className="after">
                    <span>建议</span>
                    <p>{active.after}</p>
                  </div>
                </div>
                <code className="rule-label">{active.rule}</code>
                <div className="detail-actions">
                  <Button
                    className="accept-btn"
                    disabled={busy || active.status === "accepted"}
                    onClick={() => onDecide(active, "accept")}
                  >
                    <Check size={15} />
                    接受修改
                  </Button>
                  <Button
                    variant="outline"
                    className="reject-btn"
                    disabled={busy || active.status === "rejected"}
                    onClick={() => onDecide(active, "reject")}
                  >
                    保留原文
                  </Button>
                </div>
              </section>
            )}
          </TabsContent>
          <TabsContent value="debate" className="debate-panel">
            <div className="list-label">
              <span>多视角审阅记录</span>
              <Badge variant="secondary">演示记录</Badge>
            </div>
            <div className="agent-list">
              {agents.map((agent, index) => (
                <article className="agent-row" key={agent.name}>
                  <div className={"agent-avatar agent-" + agent.tone}>
                    {agent.name[0]}
                  </div>
                  <div>
                    <div className="agent-name">
                      <strong>{agent.name}</strong>
                      <time>{agent.time}</time>
                    </div>
                    <p>{agent.text}</p>
                    <span className="agent-round">
                      第 {index < 2 ? "一" : "二"} 轮 ·{" "}
                      {index === 3 ? "评审汇总" : "独立审阅"}
                    </span>
                  </div>
                </article>
              ))}
            </div>
            <div className="judge-note">
              <MessageSquareText size={17} />
              <p>这里展示示例评审过程；实际文档修订以格式扫描结果为准。</p>
            </div>
          </TabsContent>
        </Tabs>
      </Card>
    </aside>
  );
}
