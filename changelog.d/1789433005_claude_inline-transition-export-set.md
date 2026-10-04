### Performance

- The inline-transition check tested whether each matched function was still exported by scanning the whole export table once per function, which is quadratic in library size. It now builds the set of exported names once. Found by the real-library call-count gate: 32k to 340k calls when the library grew 4x.
