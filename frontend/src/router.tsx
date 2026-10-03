import { createBrowserRouter, Navigate } from "react-router-dom";
import ProtectedRoute from "./components/ProtectedRoute";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/LoginPage";
import RunsPage from "./pages/RunsPage";
import RunDetailPage from "./pages/RunDetailPage";
import BridgePage from "./pages/BridgePage";
import PassportPage from "./pages/PassportPage";
import SettingsPage from "./pages/SettingsPage";
import ListsPage from "./pages/ListsPage";
import CuratorsPage from "./pages/CuratorsPage";
import CuratorProfilePage from "./pages/CuratorProfilePage";

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
          { path: "bridge", element: <BridgePage /> },
          { path: "passport", element: <PassportPage /> },
          { path: "lists", element: <ListsPage /> },
          { path: "curators", element: <CuratorsPage /> },
          { path: "curators/:username", element: <CuratorProfilePage /> },
          { path: "settings", element: <SettingsPage /> },
          { path: "*", element: <Navigate to="/runs" replace /> },
        ],
      },
    ],
  },
]);
