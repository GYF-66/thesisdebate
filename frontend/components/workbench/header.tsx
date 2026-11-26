"use client";

import { ArrowUpRight, FileUp, Layers3, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { WorkbenchView } from "@/lib/workbench-types";

export function WorkbenchHeader({
  view,
  onNavigate,
  onImport,
  busy,
}: {
  view: WorkbenchView;
  onNavigate: (view: WorkbenchView) => void;
  onImport: () => void;
  busy: boolean;
}) {
  const { theme, setTheme } = useTheme();
  const links = [
    { id: "workspace" as const, label: "工作台" },
    { id: "comparison" as const, label: "修订对比" },
    { id: "review" as const, label: "评审记录" },
  ];
  return (
    <header className="topbar">
      <a className="brand" href="#top" aria-label="研辩台首页">
        <span className="brand-mark">
          <Layers3 size={25} strokeWidth={1.8} />
        </span>
        <span>
          <strong>
            研辩台<span className="brand-dot">.</span>
          </strong>
          <small>ThesisDebate</small>
        </span>
      </a>
      <nav className="pill-nav" aria-label="工作台导航">
        {links.map((link) => (
          <a
            key={link.id}
            href={"#" + link.id}
            aria-current={view === link.id ? "location" : undefined}
            onClick={(event) => {
              event.preventDefault();
              onNavigate(link.id);
            }}
          >
            {link.label}
            {view === link.id && <ArrowUpRight size={14} aria-hidden="true" />}
          </a>
        ))}
      </nav>
      <div className="header-actions">
        <div className="theme-switch" role="group" aria-label="主题">
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  className="theme-light"
                  aria-label="切换浅色主题"
                  aria-pressed={theme === "light"}
                  onClick={() => setTheme("light")}
                />
              }
            >
              <Sun size={17} />
            </TooltipTrigger>
            <TooltipContent>浅色主题</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  className="theme-dark"
                  aria-label="切换深色主题"
                  aria-pressed={theme === "dark"}
                  onClick={() => setTheme("dark")}
                />
              }
            >
              <Moon size={17} />
            </TooltipTrigger>
            <TooltipContent>深色主题</TooltipContent>
          </Tooltip>
        </div>
        <Button variant="outline" className="header-import" disabled={busy} onClick={onImport}>
          <FileUp size={16} />
          导入文档
        </Button>
        <div className="profile-avatar" aria-label="本地工作区">
          研
        </div>
      </div>
    </header>
  );
}
