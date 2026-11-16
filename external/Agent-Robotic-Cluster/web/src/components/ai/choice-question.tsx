import { CheckCircle2Icon, CircleHelpIcon } from "lucide-react"
import type { ComponentProps } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type ChoiceQuestionOption = {
  label: string
  value?: string
  description?: string
}

export type ChoiceQuestionData = {
  id?: string
  question: string
  choices: ChoiceQuestionOption[]
}

export type ChoiceQuestionProps = Omit<ComponentProps<"div">, "onSelect"> & {
  data: ChoiceQuestionData
  disabled?: boolean
  onChoose: (value: string, option: ChoiceQuestionOption) => void
}

export function ChoiceQuestion({
  className,
  data,
  disabled,
  onChoose,
  ...props
}: ChoiceQuestionProps) {
  if (!data.question || data.choices.length === 0) {
    return null
  }

  return (
    <div
      className={cn(
        "not-prose mt-3 w-full rounded-lg border bg-card p-3 text-card-foreground",
        className,
      )}
      {...props}
    >
      <div className="mb-3 flex items-start gap-2">
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
          <CircleHelpIcon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2">
            <Badge variant="secondary">Choose</Badge>
          </div>
          <p className="m-0 text-sm leading-6">{data.question}</p>
        </div>
      </div>

      <div className="grid gap-2">
        {data.choices.map((choice, index) => {
          const value = (choice.value || choice.label).trim()

          return (
            <Button
              className="h-auto justify-start gap-3 whitespace-normal rounded-md border bg-background px-3 py-2 text-left hover:bg-muted"
              disabled={disabled || !value}
              key={`${choice.label}-${index}`}
              onClick={() => onChoose(value, choice)}
              type="button"
              variant="outline"
            >
              <CheckCircle2Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <span className="grid min-w-0 gap-0.5">
                <span className="font-medium text-sm leading-5">{choice.label}</span>
                {choice.description && (
                  <span className="text-muted-foreground text-xs leading-5">
                    {choice.description}
                  </span>
                )}
              </span>
            </Button>
          )
        })}
      </div>
    </div>
  )
}
