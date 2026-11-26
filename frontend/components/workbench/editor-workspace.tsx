"use client";

import { useEffect, useState } from "react";
import { EditorContent, type Editor } from "@tiptap/react";
import {
  AlignLeft,
  ArrowUpRight,
  Bold,
  BookOpen,
  Check,
  ChevronRight,
  Code2,
  FileText,
  GitCompare,
  Italic,
  ListTree,
  RotateCcw,
  Send,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { PreviewResponse } from "@/lib/workbench-types";

function readEditorState(editor: Editor | null) {
  const headings: { text: string; level: number; position: number }[] = [];
  editor?.state.doc.descendants((node, position) => {
    if (node.type.name === "heading")
      headings.push({
        text: node.textContent,
        level: Number(node.attrs.level),
        position,
      });
  });
  return {
    headings,
    characters: editor?.getText().replace(/\s/g, "").length || 0,
    bold: editor?.isActive("bold") || false,
    italic: editor?.isActive("italic") || false,
  };
}

export function EditorWorkspace({
  editor,
  documentName,
  uploadMessage,
  latex,
  showDiff,
  previewData,
  onLatexChange,
  onInsertFormula,
  onToggleDiff,
  onUndo,
  busy,
  documentReady,
}: {
  busy: boolean;
  documentReady: boolean;
  editor: Editor | null;
  documentName: string;
  uploadMessage: string;
  latex: string;
  showDiff: boolean;
  previewData: PreviewResponse | null;
  onLatexChange: (value: string) => void;
  onInsertFormula: () => void;
  onToggleDiff: () => void;
  onUndo: () => void;
}) {
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [state, setState] = useState(() => readEditorState(editor));
  useEffect(() => {
    const update = () => setState(readEditorState(editor));
    update();
    editor?.on("create", update);
    editor?.on("transaction", update);
    return () => {
      editor?.off("create", update);
      editor?.off("transaction", update);
    };
  }, [editor]);
  return (
    <div className="editor-column">
      <Card className="editor-card" id="workspace">
        <div className="editor-heading">
          <div>
            <span className="eyebrow">YOUR IDEAS, REFINED</span>
            <h2>
              专注于每一个好想法<span className="editor-title-dot">.</span>
            </h2>
          </div>
          <Badge variant="outline" className="editing-badge">
            <span className="live-dot" />
            {busy ? "处理中" : documentReady ? "编辑中" : "待转换"}
          </Badge>
        </div>
        <div className="editor-toolbar">
          <Sheet open={outlineOpen} onOpenChange={setOutlineOpen}>
            <SheetTrigger
              render={<Button variant="ghost" className="outline-trigger" />}
            >
              <ListTree size={16} />
              文档目录
            </SheetTrigger>
            <SheetContent side="left" className="outline-sheet">
              <SheetHeader>
                <SheetTitle>文档目录</SheetTitle>
                <SheetDescription>{documentName}</SheetDescription>
              </SheetHeader>
              <nav aria-label="文档章节" className="document-outline">
                {state?.headings.length ? (
                  state.headings.map((heading, index) => (
                    <button
                      key={`${heading.position}-${index}`}
                      style={{
                        paddingLeft: `${18 + Math.max(heading.level - 2, 0) * 16}px`,
                      }}
                      onClick={() => {
                        setOutlineOpen(false);
                        editor
                          ?.chain()
                          .focus()
                          .setTextSelection(heading.position + 1)
                          .scrollIntoView()
                          .run();
                      }}
                    >
                      <FileText size={14} />
                      <span>{heading.text || "未命名章节"}</span>
                      <ChevronRight size={13} />
                    </button>
                  ))
                ) : (
                  <div className="empty-state">
                    <BookOpen size={25} />
                    <p>为正文添加标题后，目录会显示在这里。</p>
                  </div>
                )}
              </nav>
            </SheetContent>
          </Sheet>
          <span className="toolbar-divider" />
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={busy || !documentReady}
                  aria-label="加粗"
                  aria-pressed={state?.bold || false}
                  onClick={() => editor?.chain().focus().toggleBold().run()}
                />
              }
            >
              <Bold size={15} />
            </TooltipTrigger>
            <TooltipContent>加粗 · Ctrl+B</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={busy || !documentReady}
                  aria-label="斜体"
                  aria-pressed={state?.italic || false}
                  onClick={() => editor?.chain().focus().toggleItalic().run()}
                />
              }
            >
              <Italic size={15} />
            </TooltipTrigger>
            <TooltipContent>斜体 · Ctrl+I</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  disabled={busy || !documentReady}
                  aria-label="正文段落"
                  onClick={() => editor?.chain().focus().setParagraph().run()}
                />
              }
            >
              <AlignLeft size={16} />
            </TooltipTrigger>
            <TooltipContent>正文段落</TooltipContent>
          </Tooltip>
          <span className="toolbar-spacer" />
          <Button
            variant="ghost"
            className="diff-toggle"
            onClick={onToggleDiff}
            aria-label={showDiff ? "关闭彩色对比" : "开启彩色对比"}
            aria-pressed={showDiff}
          >
            <GitCompare size={16} />
            <span>{showDiff ? "关闭彩色对比" : "开启彩色对比"}</span>
          </Button>
        </div>
        {uploadMessage && (
          <div role="status" className="upload-message">
            <FileText size={16} />
            <p>{uploadMessage}</p>
          </div>
        )}
        <div className="paper-stage">
          <div className="paper-context">
            <span>
              <FileText size={12} />
              {documentName}
            </span>
            <span>修订副本</span>
          </div>
          <article className="paper">
            <div className="paper-label">
              <span>REVIEW COPY</span>
              <span>研辩台 / 文稿</span>
            </div>
            <EditorContent editor={editor} />
            <div className="paper-footer">
              <span>思考、推敲，然后更进一步。</span>
              <span>THESISDEBATE</span>
            </div>
          </article>
          <div className="paper-caption">
            <span>
              <Check size={12} />
              原始文件保留
            </span>
            <span>{state?.characters || 0} 字符 · 本次会话编辑</span>
          </div>
        </div>
        <form
          className="formula-bar"
          onSubmit={(event) => {
            event.preventDefault();
            onInsertFormula();
          }}
        >
          <label htmlFor="formula-source">
            <Code2 size={17} />
            <span>LaTeX 公式</span>
          </label>
          <Input
            id="formula-source"
            value={latex}
            onChange={(event) => onLatexChange(event.target.value)}
            aria-label="LaTeX 公式源码"
            placeholder="输入 LaTeX 公式…"
          />
          <Button
            type="submit"
            variant="secondary"
            className="insert-button"
            disabled={!latex.trim() || !editor || busy || !documentReady}
          >
            <Send size={14} />
            插入公式
          </Button>
        </form>
        <div className="editor-bottom">
          <span>
            <Sparkles size={12} />
            把注意力留给内容
          </span>
          <Button variant="ghost" disabled={busy} onClick={onUndo}>
            <RotateCcw size={13} />
            撤销最近修订
          </Button>
        </div>
      </Card>
      <div id="comparison" className="comparison-anchor">
        {showDiff && <ComparisonPanel data={previewData} />}
      </div>
    </div>
  );
}

function ComparisonPanel({ data }: { data: PreviewResponse | null }) {
  const source = (data?.source || "").split(/\r?\n/);
  const preview = (data?.preview || "").split(/\r?\n/);
  const count = Math.max(source.length, preview.length);
  return (
    <Card className="comparison-card" aria-label="服务端彩色差异预览">
      <div className="card-heading">
        <h2>
          <GitCompare size={18} />
          修订对比
        </h2>
        <Badge variant="secondary">
          {data ? `已应用 ${data.applied_count || 0} 项` : "等待扫描"}
        </Badge>
      </div>
      {!data ? (
        <div className="comparison-empty">
          <div className="comparison-icon">
            <GitCompare size={26} strokeWidth={1.5} />
          </div>
          <div>
            <strong>每一处变化，都有迹可循。</strong>
            <p>运行格式扫描后，在这里并排查看原稿与修订副本。</p>
          </div>
          <ArrowUpRight size={20} />
        </div>
      ) : (
        <>
          <div className="comparison-columns">
            {[
              { label: "原稿 · 保留不变", lines: source, kind: "original" },
              {
                label: "修订预览 · 可逐项接受",
                lines: preview,
                kind: "revised",
              },
            ].map((column) => (
              <section
                className={"comparison-column " + column.kind}
                key={column.kind}
              >
                <h3>{column.label}</h3>
                <pre>
                  {Array.from({ length: count }, (_, index) => (
                    <span
                      key={index}
                      className={
                        source[index] !== preview[index]
                          ? "diff-line changed"
                          : "diff-line"
                      }
                    >
                      <span className="line-number">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      {column.lines[index] || " "}
                    </span>
                  ))}
                </pre>
              </section>
            ))}
          </div>
          {data.notes?.map((note) => (
            <p className="comparison-note" key={note}>
              提示：{note}
            </p>
          ))}
        </>
      )}
    </Card>
  );
}
