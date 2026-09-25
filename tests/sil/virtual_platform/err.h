#ifndef ERR_H_
#define ERR_H_

#define CHECK_ERR(cond, ret) do { \
    if (!(cond)) return (ret); \
} while (0)

#endif /* ERR_H_ */
