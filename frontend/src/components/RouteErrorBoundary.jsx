import { Component } from "react";

/**
 * Empêche une erreur de rendu (lazy load, markdown, etc.) de vider toute l’app.
 */
export default class RouteErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("[LecturAI] RouteErrorBoundary", error, info?.componentStack);
  }

  render() {
    const { error } = this.state;
    const { children, fallback } = this.props;
    if (error) {
      return typeof fallback === "function" ? fallback(error) : fallback;
    }
    return children;
  }
}
