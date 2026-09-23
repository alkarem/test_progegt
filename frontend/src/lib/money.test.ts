import { D, fmt, isNeg, MONEY_RE, normalizeMoneyInput, pct } from "./money";

describe("fmt", () => {
  it("يعرض ثلاث خانات عشرية مع فواصل الآلاف", () => {
    expect(fmt("1234567.5")).toBe("1,234,567.500");
    expect(fmt("0")).toBe("0.000");
  });
  it("يعرض السالب بين قوسين افتراضيًا وبإشارة عند الطلب", () => {
    expect(fmt("-18370")).toBe("(18,370.000)");
    expect(fmt("-24969.42", { parens: false })).toBe("-24,969.420");
  });
  it("يعرض شرطة للقيم الغائبة", () => {
    expect(fmt(null)).toBe("—");
    expect(fmt(undefined)).toBe("—");
    expect(fmt("")).toBe("—");
  });
  it("لا يفقد الدقة في المبالغ الكبيرة (لا تحويل إلى number)", () => {
    expect(fmt("999999999999999.999")).toBe("999,999,999,999,999.999");
  });
});

describe("normalizeMoneyInput", () => {
  it("يحول الأرقام العربية الهندية والفواصل", () => {
    expect(normalizeMoneyInput("١٢٬٥٠٠٫٧٥")).toBe("12500.75");
    expect(normalizeMoneyInput("12,500.750")).toBe("12500.750");
    expect(normalizeMoneyInput(" 3 000 ")).toBe("3000");
  });
});

describe("MONEY_RE", () => {
  it("يقبل حتى ثلاث خانات عشرية فقط ويرفض السالب", () => {
    expect(MONEY_RE.test("100")).toBe(true);
    expect(MONEY_RE.test("100.125")).toBe(true);
    expect(MONEY_RE.test("100.1255")).toBe(false);
    expect(MONEY_RE.test("-5")).toBe(false);
    expect(MONEY_RE.test("abc")).toBe(false);
  });
});

describe("helpers", () => {
  it("حساب عشري دقيق", () => {
    expect(D("0.1").plus("0.2").toFixed(3)).toBe("0.300");
    expect(isNeg("-0.001")).toBe(true);
    expect(isNeg("0")).toBe(false);
    expect(pct("41.666")).toBe("41.67%");
    expect(pct(null)).toBe("—");
  });
});
