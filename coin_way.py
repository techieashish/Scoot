COINS = [1, 2, 5, 10]


def count(memo, n, k, A):
    if (n, k) in memo:
        return memo[(n, k)]

    elif k == 0:
        return int(n <= A[0] * COINS[0] and n % COINS[0] == 0)

    else:
        i, total = 0, 0

        while i <= A[k] and i * COINS[k] <= n:
            total += count(memo, n - i * COINS[k], k - 1, arr)
            i += 1

        memo[(n, k)] = total
        return total


if __name__ == '__main__':
    N = 15
    arr = [2, 2, 1, 1]
    memo = {}
    print(count(memo, N, len(COINS) - 1, arr))