import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
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
        soft: "0 4px 24px -4px rgba(15, 23, 42, 0.08), 0 8px 32px -8px rgba(15, 23, 42, 0.12)",
        "soft-lg":
          "0 12px 40px -12px rgba(15, 23, 42, 0.12), 0 4px 16px -4px rgba(15, 23, 42, 0.08)",
        glow: "0 0 0 1px rgba(99, 102, 241, 0.15), 0 20px 50px -20px rgba(79, 70, 229, 0.35)",
      },
      colors: {
        brand: {
          50: "#eef2ff",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          900: "#312e81",
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
