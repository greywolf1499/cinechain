import { createBrowserRouter, Navigate, useLocation } from "react-router-dom";
import ProtectedRoute from "./components/ProtectedRoute";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/LoginPage";
import RunsPage from "./pages/RunsPage";
import RunDetailPage from "./pages/RunDetailPage";
import BridgePage from "./pages/BridgePage";
import DailyBridgePage from "./pages/DailyBridgePage";
import PassportPage from "./pages/PassportPage";
import ToolsPage from "./pages/ToolsPage";
import BingoPage from "./pages/BingoPage";
import MarathonRouterPage from "./pages/MarathonRouterPage";
import MapPage from "./pages/MapPage";
import SettingsLayout from "./pages/settings/SettingsLayout";
import {
  EngineSettings,
  GeneralSettings,
  IntegrationsSettings,
  TasksSettings,
} from "./pages/settings/SettingsPages";
import ListsPage from "./pages/ListsPage";
import CuratorsPage from "./pages/CuratorsPage";
import CuratorProfilePage from "./pages/CuratorProfilePage";

/** Old `/bridge?...` links (bookmarks, history) keep working under the Tools hub. */
function LegacyBridgeRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/tools/bridge${search}`} replace />;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    element: <ProtectedRoute />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="/runs" replace /> },
          { path: "runs", element: <RunsPage /> },
          { path: "runs/:id", element: <RunDetailPage /> },
          { path: "tools", element: <ToolsPage /> },
          { path: "tools/bridge", element: <BridgePage /> },
          { path: "tools/daily", element: <DailyBridgePage /> },
          { path: "tools/bingo", element: <BingoPage /> },
          { path: "tools/map", element: <MapPage /> },
          { path: "tools/router", element: <MarathonRouterPage /> },
          { path: "bridge", element: <LegacyBridgeRedirect /> },
          { path: "passport", element: <PassportPage /> },
          { path: "lists", element: <ListsPage /> },
          { path: "curators", element: <CuratorsPage /> },
          { path: "curators/:username", element: <CuratorProfilePage /> },
          {
            path: "settings",
            element: <SettingsLayout />,
            children: [
              { index: true, element: <Navigate to="general" replace /> },
              { path: "general", element: <GeneralSettings /> },
              { path: "engine", element: <EngineSettings /> },
              { path: "integrations", element: <IntegrationsSettings /> },
              { path: "tasks", element: <TasksSettings /> },
              { path: "*", element: <Navigate to="general" replace /> },
            ],
          },
          { path: "*", element: <Navigate to="/runs" replace /> },
        ],
      },
    ],
  },
]);
