def subset(array, num):
    result = []

    def find(arr, num, path=()):
        if not arr:
            return
        if arr[0] == num:
            result.append(path + (arr[0],))
        else:
            find(arr[1:], num - arr[0], path + (arr[0],))
            find(arr[1:], num, path)
    find(array, num)
    return result

if __name__ == '__main__':
    arr = [3, 7, 12, 18]
    target = 10
    print(subset(arr, target))




