/** رموز التصميم (07-ui-screen-map §1) */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: "#0F3D5E", 50: "#EEF4F8", 100: "#D6E4EE", 600: "#15507A", 700: "#0F3D5E", 900: "#0A2A41" },
        ok: { DEFAULT: "#1E7F5C", 50: "#E8F5EF" },
        warn: { DEFAULT: "#B7791F", 50: "#FBF3E4" },
        bad: { DEFAULT: "#B42318", 50: "#FDECEA" },
        surface: "#F6F7F9",
        line: "#DDE2E8",
        ink: { DEFAULT: "#1B2330", soft: "#4A5565", mute: "#7A8594" },
      },
      fontFamily: { sans: ["Plex", "system-ui", "sans-serif"] },
      boxShadow: { card: "0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.08)" },
    },
  },
  plugins: [],
};
