import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactElement } from "react";
import { Spinner } from "./components/ui";
import { AuthProvider, homeFor, useAuth } from "./lib/auth";
import type { Role } from "./lib/types";
import Admin from "./pages/Admin";
import Chat from "./pages/Chat";
import Login from "./pages/Login";
import StudentCourses from "./pages/StudentCourses";
import TeacherCourse from "./pages/TeacherCourse";
import TeacherHome from "./pages/TeacherHome";
import "./styles.css";

/** Screens are only navigation: every API call they make is authorised again on the server (SDS 7.2). */
function Guard({ roles, children }: { roles: Role[]; children: ReactElement }) {
  const { user, ready } = useAuth();
  const loc = useLocation();
  if (!ready) return <main className="page"><Spinner /></main>;
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  if (!roles.includes(user.role)) return <Navigate to={homeFor(user)} replace />;
  return children;
}

function Home() {
  const { user, ready } = useAuth();
  if (!ready) return <main className="page"><Spinner /></main>;
  return <Navigate to={user ? homeFor(user) : "/login"} replace />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/courses" element={<Guard roles={["student"]}><StudentCourses /></Guard>} />
          <Route path="/courses/:courseId" element={<Guard roles={["student", "teacher", "admin"]}><Chat /></Guard>} />
          <Route path="/teacher" element={<Guard roles={["teacher"]}><TeacherHome /></Guard>} />
          <Route path="/teacher/courses/:courseId" element={<Guard roles={["teacher", "admin"]}><TeacherCourse /></Guard>} />
          <Route path="/admin" element={<Guard roles={["admin"]}><Admin /></Guard>} />
          <Route path="*" element={<Home />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
);
