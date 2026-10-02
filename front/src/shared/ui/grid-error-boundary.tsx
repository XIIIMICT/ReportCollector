"use client";

import { Component, type ReactNode } from "react";

interface Props {
    children: ReactNode;
    fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
    error: Error | null;
}

export class GridErrorBoundary extends Component<Props, State> {
    constructor(props: Props) {
        super(props);
        this.state = { error: null };
    }

    static getDerivedStateFromError(error: Error): State {
        return { error };
    }

    componentDidCatch(error: Error, errorInfo: unknown) {
        console.error("Grid error caught:", error, errorInfo);
    }

    render() {
        if (this.state.error) {
            const reset = () => this.setState({ error: null });

            if (this.props.fallback) {
                return this.props.fallback(this.state.error, reset);
            }

            return (
                <div className="flex flex-col items-center justify-center gap-4 p-8 text-center">
                    <div className="text-danger text-lg font-medium">Grid Error</div>
                    <div className="text-default-500 text-sm">
                        {this.state.error.message || "An error occurred while rendering the grid"}
                    </div>
                    <button
                        onClick={reset}
                        className="bg-primary text-primary-foreground hover:bg-primary/90 rounded-lg px-4 py-2 text-sm font-medium"
                    >
                        Retry
                    </button>
                </div>
            );
        }

        return this.props.children;
    }
}
