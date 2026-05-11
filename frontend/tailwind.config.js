import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      screens: {
        /** Entre téléphone étroit et sm : grille 2 colonnes, réglages côte à côte. */
        xs: "400px",
      },
      fontFamily: {
        sans: [
          "Cairo",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        display: [
          "Plus Jakarta Sans",
          "Cairo",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
      },
      boxShadow: {
        soft: "0 4px 24px -4px rgba(120, 53, 15, 0.06), 0 8px 32px -8px rgba(15, 23, 42, 0.1)",
        "soft-lg":
          "0 12px 40px -12px rgba(194, 65, 12, 0.08), 0 4px 16px -4px rgba(15, 23, 42, 0.08)",
        glow: "0 0 0 1px rgba(249, 115, 22, 0.2), 0 20px 50px -18px rgba(234, 88, 12, 0.38)",
      },
      colors: {
        brand: {
          50: "#fff7ed",
          100: "#ffedd5",
          200: "#fed7aa",
          300: "#fdba74",
          400: "#fb923c",
          500: "#f97316",
          600: "#ea580c",
          700: "#c2410c",
          800: "#9a3412",
          900: "#7c2d12",
          950: "#431407",
        },
      },
      keyframes: {
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        celebrate: {
          "0%, 100%": { transform: "scale(1)" },
          "40%": { transform: "scale(1.08)" },
          "70%": { transform: "scale(0.96)" },
        },
        flip: {
          "0%": { transform: "rotateY(0deg)" },
          "100%": { transform: "rotateY(180deg)" },
        },
        "blob-drift": {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "25%": { transform: "translate(28px, -18px) scale(1.04)" },
          "50%": { transform: "translate(-22px, 14px) scale(0.98)" },
          "75%": { transform: "translate(14px, 26px) scale(1.02)" },
        },
        "fade-in-up": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "toast-in": {
          "0%": { opacity: "0", transform: "translateY(14px) scale(0.97)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        breathe: {
          "0%, 100%": { opacity: "0.45", transform: "scale(1)" },
          "50%": { opacity: "0.75", transform: "scale(1.03)" },
        },
        "slide-in-right": {
          "0%": { opacity: "0", transform: "translateX(28px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        "slide-in-left": {
          "0%": { opacity: "0", transform: "translateX(-28px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        "ring-spin-slow": {
          "0%": { transform: "rotate(0deg)" },
          "100%": { transform: "rotate(360deg)" },
        },
      },
      animation: {
        shimmer: "shimmer 1.8s linear infinite",
        celebrate: "celebrate 0.85s ease-in-out both",
        "blob-drift": "blob-drift 22s ease-in-out infinite",
        "blob-drift-reverse": "blob-drift 28s ease-in-out infinite reverse",
        "fade-in-up": "fade-in-up 0.55s ease-out both",
        "toast-in": "toast-in 0.42s cubic-bezier(0.16, 1, 0.3, 1) both",
        breathe: "breathe 5s ease-in-out infinite",
        "slide-in-right": "slide-in-right 0.42s cubic-bezier(0.16, 1, 0.3, 1) both",
        "slide-in-left": "slide-in-left 0.42s cubic-bezier(0.16, 1, 0.3, 1) both",
        "ring-spin-slow": "ring-spin-slow 8s linear infinite",
      },
    },
  },
  plugins: [typography],
};
