import { fmtDate, fmtDateTime } from "./labels";

describe("التواريخ", () => {
  it("fmtDate يعرض يوم/شهر/سنة", () => {
    expect(fmtDate("2026-03-05")).toBe("05/03/2026");
    expect(fmtDate(null)).toBe("—");
  });
  it("fmtDateTime بصيغة ثابتة لا تتأثر باتجاه النص", () => {
    const d = new Date(2026, 8, 23, 11, 5);
    expect(fmtDateTime(d.toISOString())).toBe("23/09/2026 11:05");
    expect(fmtDateTime(undefined)).toBe("—");
  });
});
