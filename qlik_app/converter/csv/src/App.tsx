import Header from "./components/Header/Header";
import Stepper from "../src/Stepper/Stepper";
import AppRoutes from "../src/router/AppRouter";
import Footer from "./components/Footer/Footer";

export default function App() {
  return (
    <div className="app-layout">
      <Header />
      <Stepper />

      <main className="app-main">
        <AppRoutes />
      </main>

      <Footer />
    </div>
  );
}