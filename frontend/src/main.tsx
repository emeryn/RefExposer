import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { MantineProvider, createTheme, localStorageColorSchemeManager } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import './styles.css';
import App from './App';
import { AuthProvider } from './auth/AuthContext';

// Dark palette: blue-tinted slate (instead of Mantine's neutral grey), from text (0) to page background (9).
// Contrast checked on the surfaces actually used: text 0 on 7 ≈ 13:1, dimmed 2 on 7 ≈ 6.6:1 (WCAG AA).
const darkSlate = [
  '#E4E8EF', // 0  main text
  '#C3CAD6', // 1
  '#9BA5B6', // 2  secondary text (dimmed)
  '#8590A3', // 3  placeholders (4.5:1 on inputs)
  '#3A4356', // 4  borders
  '#2B3243', // 5  hover
  '#222838', // 6  inputs, raised controls
  '#1A1F2C', // 7  cards, header, navbar (body)
  '#131722', // 8  page background
  '#0D1018', // 9
] as const;

const theme = createTheme({
  primaryColor: 'indigo',
  primaryShade: { light: 6, dark: 5 },
  colors: { dark: [...darkSlate] },
  defaultRadius: 'md',
  fontFamily: 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  fontFamilyMonospace: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  headings: { fontWeight: '650' },
  components: {
    Card: { defaultProps: { withBorder: true, radius: 'md' } },
    Paper: { defaultProps: { radius: 'md' } },
    Tooltip: { defaultProps: { withArrow: true, openDelay: 250 } },
  },
});

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <MantineProvider
      theme={theme}
      defaultColorScheme="auto"
      colorSchemeManager={localStorageColorSchemeManager({ key: 'refexposer-color-scheme' })}
    >
      <Notifications position="top-right" />
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
);
