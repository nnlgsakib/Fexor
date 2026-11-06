import "@fontsource/schibsted-grotesk/400.css";
import "@fontsource/schibsted-grotesk/500.css";
import "@fontsource/schibsted-grotesk/600.css";
import "@fontsource/schibsted-grotesk/700.css";
import "@mantine/core/styles.css";
import "@mantine/dates/styles.css";
import "@mantine/charts/styles.css";
import "@mantine/notifications/styles.css";
import "@mantine/dropzone/styles.css";
import "./app.css";

import { MantineProvider } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AuthProvider, useMe } from "./auth";
import { Layout } from "./Layout";
import { Account, Admins, Audit, Integrations } from "./pages/Org";
import { Activity, Review } from "./pages/Activity";
import { Cards } from "./pages/Cards";
import { Devices } from "./pages/Devices";
import { Overview } from "./pages/Overview";
import { People } from "./pages/People";
import { Register } from "./pages/Register";
import { Schedule } from "./pages/Schedule";
import { Settings } from "./pages/Settings";
import { Spaces } from "./pages/Spaces";
import { theme } from "./theme";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: true, retry: 1, staleTime: 10_000 } },
});

function Home() {
  const { me } = useMe();
  return <Navigate to={me.spaces.length === 1 ? `/spaces/${me.spaces[0].id}` : "/spaces"} replace />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="light">
      <Notifications position="top-right" />
      <ModalsProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <Routes>
              <Route path="/spaces/:spaceId/cards" element={<Cards />} />
              <Route element={<Layout />}>
                <Route index element={<Home />} />
                <Route path="/spaces" element={<Spaces />} />
                <Route path="/spaces/:spaceId" element={<Overview />} />
                <Route path="/spaces/:spaceId/register" element={<Register />} />
                <Route path="/spaces/:spaceId/people" element={<People />} />
                <Route path="/spaces/:spaceId/activity" element={<Activity />} />
                <Route path="/spaces/:spaceId/review" element={<Review />} />
                <Route path="/spaces/:spaceId/schedule" element={<Schedule />} />
                <Route path="/spaces/:spaceId/devices" element={<Devices />} />
                <Route path="/spaces/:spaceId/settings" element={<Settings />} />
                <Route path="/admins" element={<Admins />} />
                <Route path="/integrations" element={<Integrations />} />
                <Route path="/audit" element={<Audit />} />
                <Route path="/account" element={<Account />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
            </Routes>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
      </ModalsProvider>
    </MantineProvider>
  </StrictMode>,
);
