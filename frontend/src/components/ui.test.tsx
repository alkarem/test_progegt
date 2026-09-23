import { render, screen } from "@testing-library/react";
import { Money, Table } from "./ui";

describe("Money", () => {
  it("يلون السالب ويعرضه بين قوسين", () => {
    render(<Money v="-300" />);
    const el = screen.getByText("(300.000)");
    expect(el.className).toMatch(/text-bad/);
  });
});

describe("Table", () => {
  it("يعرض الصفوف ورسالة الفراغ", () => {
    const { rerender } = render(<Table cols={[{ key: "a", title: "أ" }]} rows={[{ a: "قيمة" }]} />);
    expect(screen.getByText("قيمة")).toBeInTheDocument();
    rerender(<Table cols={[{ key: "a", title: "أ" }]} rows={[]} empty="لا شيء" />);
    expect(screen.getByText("لا شيء")).toBeInTheDocument();
  });
});
