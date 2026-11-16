// /teacher — UC4 Robot Teacher page (sim)
// คุยหาหัวข้อ → generate โจทย์ → MCQ + demo บน WorkspacePanel
import { MoonIcon, SunIcon } from "lucide-react"
import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { TeacherPanel } from "@/components/robot/TeacherPanel"
import { WorkspacePanel } from "@/components/robot/WorkspacePanel"
import { Button } from "@/components/ui/button"
import { TooltipProvider } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import "@/styles.css"

type Theme = "light" | "dark"

export default function TeacherPage() {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const stored = localStorage.getItem("openclaude-theme")
      return stored === "dark" || stored === "light" ? stored : "light"
    } catch {
      return "light"
    }
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    try {
      localStorage.setItem("openclaude-theme", theme)
    } catch {
      /* ignore */
    }
  }, [theme])

  return (
    <TooltipProvider>
      <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
        <header className="shrink-0 border-b bg-background/95 backdrop-blur">
          <div className="mx-auto flex h-14 w-full max-w-[90rem] items-center justify-between gap-3 px-3 sm:px-5">
            <div className="flex min-w-0 items-center gap-3">
              <img alt="" className="size-8 shrink-0 rounded-md" src="/openclaude.png" />
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h1 className="truncate font-semibold text-sm">ครูหุ่นยนต์ · UC4</h1>
                  <span className="hidden sm:inline rounded bg-sky-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
                    /teacher
                  </span>
                </div>
                <div className="truncate text-muted-foreground text-xs">
                  คุย → ออกโจทย์ → เรียนบน sim · demo ล็อก skeleton
                </div>
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-2">
              <Link
                to="/"
                className={cn(
                  "rounded-md border px-3 py-1.5 text-xs font-semibold hover:bg-accent",
                )}
              >
                ห้องเรียน ARC
              </Link>
              <Button
                onClick={() => setTheme(current => (current === "dark" ? "light" : "dark"))}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                {theme === "dark" ? <SunIcon className="size-4" /> : <MoonIcon className="size-4" />}
                <span className="sr-only">สลับธีม</span>
              </Button>
            </div>
          </div>
        </header>

        <main className="mx-auto flex min-h-0 w-full max-w-[90rem] flex-1 gap-3 p-3 sm:p-4">
          {/* ซ้าย: แผงครู */}
          <section className="flex min-h-0 w-full max-w-md shrink-0 flex-col sm:w-[22rem] lg:w-[26rem]">
            <div className="min-h-0 flex-1 overflow-auto rounded-xl border bg-card p-3 shadow-sm">
              <TeacherPanel />
            </div>
          </section>

          {/* ขวา: 3D รีใช้ของเดิม */}
          <section className="hidden min-h-0 min-w-0 flex-1 md:flex">
            <div className="min-h-0 w-full flex-1 overflow-hidden rounded-xl border bg-card p-2 shadow-sm">
              <WorkspacePanel className="h-full" />
            </div>
          </section>
        </main>

        {/* mobile: 3D ด้านล่าง */}
        <div className="min-h-[40vh] border-t p-2 md:hidden">
          <WorkspacePanel className="h-full min-h-[40vh]" />
        </div>
      </div>
    </TooltipProvider>
  )
}
