export class StaleMutationScopeError extends Error {
    constructor() {
        super("Изменение относится к предыдущему срезу.");
        this.name = "StaleMutationScopeError";
    }
}

type Tail = Promise<void>;

/**
 * A synchronous per-scope promise queue. Reserving the tail happens before
 * the caller yields, so two callbacks from one render cannot start together.
 * Switching scope invalidates queued work before it reaches the task factory.
 */
export class ScopedMutationQueue {
    private currentScope: string | null = null;

    private generation = 0;

    private tail: Tail = Promise.resolve();

    setScope(scope: string): void {
        if (this.currentScope === scope) return;
        this.currentScope = scope;
        this.generation += 1;
    }

    enqueue<T>(scope: string, task: () => Promise<T>): Promise<T> {
        if (this.currentScope !== scope) {
            return Promise.reject(new StaleMutationScopeError());
        }
        const generation = this.generation;
        const previous = this.tail;
        const run = previous.then(() => {
            if (this.currentScope !== scope || this.generation !== generation) {
                throw new StaleMutationScopeError();
            }
            return task();
        });
        // A rejected item must not stop the next item in the same scope.
        this.tail = run.then(
            () => undefined,
            () => undefined,
        );
        return run;
    }

    whenIdle(): Promise<void> {
        return this.tail;
    }
}
