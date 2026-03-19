import "./Footer.css";

export default function Footer() {
  return (
    <footer className="app-footer">
      <div className="footer-content">
        <span>© {new Date().getFullYear()} QlikAI</span>
        <span className="sep"> - </span>
        <a href="https://sorim.ai" target="_blank" rel="noreferrer">Sorim.AI</a>
      </div>
    </footer>
  );
}
