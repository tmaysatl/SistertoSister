export type IntakeField = {
  key: string;
  label: string;
  type: "text" | "longtext" | "date" | "select" | "radio" | "checkbox" | "consent";
  required?: boolean;
  options?: string[];
};
export type IntakeSection = {
  title: string;
  show_if?: { key: string; equals: string };
  fields: IntakeField[];
};
export type IntakeSchema = { sections: IntakeSection[] };
export type IntakeFormDef = { key: string; title: string; description: string; schema: IntakeSchema };

export const STATUSES = ["new", "contacted", "scheduled", "closed"] as const;

export function sectionVisible(sec: IntakeSection, values: Record<string, any>) {
  return !sec.show_if || values[sec.show_if.key] === sec.show_if.equals;
}
