/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./index.html",
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['Nunito', 'ui-rounded', 'sans-serif'],
        alfa: ['Nunito', 'ui-rounded', 'sans-serif'],
        archivo: ['Nunito', 'sans-serif'],
        script: ['Caveat', 'cursive'],
      },
      colors: {
        primary: "var(--primary)",
        secondary: "var(--secondary)",
        background: "var(--background)",
        text: "var(--text)",
        accent: "var(--accent)",
        paw: {
          text: "var(--text)",
          bg: "var(--background)",
          primary: "var(--primary)",
          secondary: "var(--secondary)",
          accent: "var(--accent)",
          coral: "var(--paw-coral)",
          surface: "var(--paw-surface)",
        },
      },
    },
  },
  plugins: [],
};
