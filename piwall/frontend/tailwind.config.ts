import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        pit: {
          black: "#0a0a0a",
          dark: "#111111",
          card: "#161616",
          surface: "#1a1a1a",
          border: "#2a2a2a",
          muted: "#3a3a3a",
          text: "#a0a0a0",
          light: "#e0e0e0",
        },
        f1: {
          red: "#e10600",
          redHover: "#ff1801",
          redDark: "#8b0000",
          blue: "#0057ff",
          blueDark: "#003ecb",
        },
        compound: {
          soft: "#FF3333",
          medium: "#FFD700",
          hard: "#CCCCCC",
          inter: "#43B02A",
          wet: "#0067B1",
        },
      },
      fontFamily: {
        sans: ['"Inter"', '"Helvetica Neue"', 'Arial', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
        display: ['"Inter"', '"Helvetica Neue"', 'Arial', 'sans-serif'],
      },
      fontSize: {
        'hero': ['4rem', { lineHeight: '1', letterSpacing: '-0.04em', fontWeight: '800' }],
        'title': ['2rem', { lineHeight: '1.1', letterSpacing: '-0.03em', fontWeight: '700' }],
        'subtitle': ['1.25rem', { lineHeight: '1.3', letterSpacing: '-0.02em', fontWeight: '500' }],
      },
      borderRadius: {
        'card': '12px',
      },
      boxShadow: {
        'card': '0 1px 3px rgba(0,0,0,0.3), 0 4px 12px rgba(0,0,0,0.2)',
        'card-hover': '0 4px 16px rgba(0,0,0,0.4), 0 8px 32px rgba(0,0,0,0.3)',
        'glow-red': '0 0 20px rgba(225, 6, 0, 0.15)',
        'glow-blue': '0 0 20px rgba(0, 87, 255, 0.15)',
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(ellipse at center, var(--tw-gradient-stops))',
        'grid-pattern': 'linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.5s ease-out',
        'slide-up': 'slideUp 0.4s ease-out',
        'f1-rev': 'f1Rev 0.3s ease-in-out',
        'f1-hero-race': 'f1HeroRace 3s cubic-bezier(0.25, 0.1, 0.25, 1) 0.3s forwards',
        'f1-accent': 'f1AccentBounce 2s ease-in-out infinite',
        'speed-line-1': 'speedLine 0.6s ease-in-out infinite',
        'speed-line-2': 'speedLine 0.8s ease-in-out infinite 0.1s',
        'speed-line-3': 'speedLine 0.7s ease-in-out infinite 0.2s',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        f1Rev: {
          '0%': { transform: 'translateX(0)' },
          '50%': { transform: 'translateX(3px)' },
          '100%': { transform: 'translateX(0)' },
        },
        f1HeroRace: {
          '0%': { transform: 'translateX(-550px) translateY(-50%)', opacity: '0' },
          '10%': { opacity: '1' },
          '70%': { opacity: '1' },
          '100%': { transform: 'translateX(100vw) translateY(-50%)', opacity: '0' },
        },
        f1AccentBounce: {
          '0%, 100%': { transform: 'translateX(0)' },
          '50%': { transform: 'translateX(3px)' },
        },
        speedLine: {
          '0%, 100%': { opacity: '0.15', transform: 'scaleX(1)' },
          '50%': { opacity: '0.5', transform: 'scaleX(1.5)' },
        },
      },
    },
  },
  plugins: [],
};
export default config;
