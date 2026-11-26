"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, CheckCheck, FileText, Plus, WandSparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardAction, CardContent, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Carousel, CarouselContent, CarouselItem, CarouselPrevious, CarouselNext, type CarouselApi } from "@/components/ui/carousel";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Change, DocumentSession } from "@/lib/workbench-types";

export function Overview({
  changes, score, documents, activeDocumentId, documentReady, scanState,
  runId, busy, onScan, onImport, onReview, onSelectDocument,
}: {
  changes: Change[];
  score: number;
  documents: DocumentSession[];
  activeDocumentId: string;
  documentReady: boolean;
  scanState: string;
  runId: string | null;
  busy: boolean;
  onScan: () => void;
  onImport: () => void;
  onReview: () => void;
  onSelectDocument: (id: string) => void;
}) {
  const pending = changes.filter((item) => item.status === "pending").length;
  const completed = changes.length - pending;
  return (
    <section className="overview-grid" aria-label="评审概览">
      <Card className="hero-card">
        <CardHeader>
          <CardTitle>论文评审概览</CardTitle>
          <CardAction><Badge className="demo-badge">演示评分</Badge></CardAction>
        </CardHeader>
        <CardContent className="hero-content">
          <div className="hero-score"><strong data-testid="review-score">{score}</strong><span>/ 100</span></div>
          <p className="hero-caption">每一次修订，都让表达更进一步。</p>
        </CardContent>
        <CardFooter className="hero-bottom">
          <Button className="scan-button" onClick={onScan} disabled={busy || !documentReady}>
            <WandSparkles size={16} />{scanState || "运行格式扫描"}<ArrowUpRight size={15} />
          </Button>
          <span className="hero-note">{runId ? "本次扫描已连接" : "从当前文档开始"}</span>
        </CardFooter>
      </Card>
      <div className="metrics-stack">
        <Card className="metric-card" size="sm">
          <CardHeader>
            <CardDescription>待处理建议</CardDescription>
            <CardAction><ArrowUpRight size={16} className="text-muted-foreground" /></CardAction>
          </CardHeader>
          <CardContent className="metric-content">
            <div className="metric-value"><strong data-testid="pending-count">{String(pending).padStart(2, "0")}</strong><span>项建议</span></div>
            <Button variant="ghost" size="sm" className="metric-link" onClick={onReview}>{pending ? "查看建议" : "已处理完毕"}<ArrowUpRight size={13} /></Button>
          </CardContent>
        </Card>
        <Card className="metric-card" size="sm">
          <CardHeader>
            <CardDescription>本轮已处理</CardDescription>
            <CardAction><CheckCheck size={16} className="text-muted-foreground" /></CardAction>
          </CardHeader>
          <CardContent>
            <div className="metric-value"><strong data-testid="completed-count">{String(completed).padStart(2, "0")}</strong><span>/ {changes.length} 项</span></div>
            <div className="completion-track" role="progressbar" aria-label="建议处理进度" aria-valuenow={completed} aria-valuemin={0} aria-valuemax={changes.length || 1}>
              <i style={{ width: `${changes.length ? (completed / changes.length) * 100 : 0}%` }} />
            </div>
          </CardContent>
        </Card>
      </div>
      <DocumentCarousel key={documents.length} documents={documents} activeId={activeDocumentId} busy={busy} onSelect={onSelectDocument} onImport={onImport} />
    </section>
  );
}

function DocumentCarousel({ documents, activeId, busy, onSelect, onImport }: {
  documents: DocumentSession[];
  activeId: string;
  busy: boolean;
  onSelect: (id: string) => void;
  onImport: () => void;
}) {
  const [api, setApi] = useState<CarouselApi>();
  const index = documents.findIndex((document) => document.id === activeId);
  const initialIndex = useRef(index);
  const state = useRef({ documents, activeId, busy, onSelect });
  useEffect(() => { state.current = { documents, activeId, busy, onSelect }; });
  const opts = useMemo(() => ({ align: "start" as const, startIndex: initialIndex.current, watchDrag: !busy }), [busy]);

  useEffect(() => {
    if (!api) return;
    const selected = () => {
      const current = state.current;
      const document = current.documents[api.selectedScrollSnap()];
      if (current.busy) {
        api.scrollTo(current.documents.findIndex((item) => item.id === current.activeId), true);
      } else if (document && document.id !== current.activeId) current.onSelect(document.id);
    };
    const reset = () => api.scrollTo(state.current.documents.findIndex((item) => item.id === state.current.activeId), true);
    reset();
    api.on("select", selected);
    api.on("reInit", reset);
    return () => { api.off("select", selected); api.off("reInit", reset); };
  }, [api]);
  useEffect(() => {
    if (api && api.selectedScrollSnap() !== index) {
      api.scrollTo(index, window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    }
  }, [api, index]);

  return (
    <Card className="document-overview">
      <CardHeader>
        <CardTitle className="document-title">我的文档 <Badge variant="secondary" data-testid="document-count">{documents.length}</Badge></CardTitle>
        <CardDescription>切换文档，继续上次编辑</CardDescription>
        <CardAction><Button variant="outline" size="icon" onClick={onImport} disabled={busy} aria-label="导入新文档"><Plus /></Button></CardAction>
      </CardHeader>
      <CardContent className="document-content">
        <Carousel setApi={setApi} opts={opts} aria-label="文档切换" tabIndex={0} className="document-carousel"
          onKeyDownCapture={(event) => {
            if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
            event.preventDefault();
            if (!busy) event.key === "ArrowLeft" ? api?.scrollPrev() : api?.scrollNext();
          }}>
          <CarouselContent className="ml-0">
            {documents.map((document, i) => (
              <CarouselItem className="pl-0" key={document.id} aria-label={`${i + 1} / ${documents.length}：${document.name}`} aria-hidden={document.id !== activeId}>
                <div className="document-preview" data-testid="document-preview">
                  <div className="document-preview-top"><span className="file-icon"><FileText size={22} /></span><Badge variant="outline">{document.type === "markdown" ? "MD" : document.type.toUpperCase()}</Badge></div>
                  <strong title={document.name}>{document.name.replace(/\.[^.]+$/, "")}</strong>
                  <div className="document-preview-meta"><span>{document.ready ? "可编辑" : "待转换"}</span><span>{document.changes.length ? `${document.changes.filter((item) => item.status === "pending").length} 项待处理` : "尚未扫描"}</span></div>
                </div>
              </CarouselItem>
            ))}
          </CarouselContent>
          <div className="document-controls">
            <span className="document-position" aria-live="polite" data-testid="document-position">{String(index + 1).padStart(2, "0")} <span>/ {String(documents.length).padStart(2, "0")}</span></span>
            <span className="document-hint">{busy ? "正在处理…" : documents.length > 1 ? "拖动或点击切换" : "导入后可切换"}</span>
            <CarouselPrevious className="static m-0 rounded-lg" disabled={busy || index <= 0} />
            <CarouselNext className="static m-0 rounded-lg" disabled={busy || index === documents.length - 1} />
          </div>
        </Carousel>
      </CardContent>
      <CardFooter className="document-selector">
        <Select value={activeId} onValueChange={(id) => { if (id) onSelect(id); }} disabled={busy}
          items={documents.map((document) => ({ value: document.id, label: document.name }))}>
          <SelectTrigger aria-label="选择文档" className="w-full min-w-0">
            <FileText size={14} /><SelectValue className="min-w-0 truncate" />
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false}>
            {documents.map((document, i) => <SelectItem key={document.id} value={document.id}><span className="document-option">{i + 1}. {document.name}</span></SelectItem>)}
          </SelectContent>
        </Select>
      </CardFooter>
    </Card>
  );
}
