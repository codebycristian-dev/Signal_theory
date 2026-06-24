# Detector Binario MLP

Proyecto en Jupyter Notebook para construir y evaluar un detector binario basado en una red neuronal MLP implementada desde cero con NumPy. El objetivo es estimar la probabilidad posterior `P(Y=1 | X=x)` y tomar decisiones binarias sin entregar a la red las probabilidades, medias o varianzas reales usadas para generar los datos.

## Archivo principal

- `Detector_binario_MLP (1).ipynb`: notebook completo con generacion de datos, entrenamiento, seleccion de umbral, evaluacion y comparacion contra detectores de referencia.

## Objetivo del proyecto

El notebook estudia un problema de deteccion binaria donde una observacion escalar `X` pertenece a una de dos clases `Y=0` o `Y=1`. La MLP aprende una regla de decision usando solo datos de entrenamiento y luego se evalua sobre muestras independientes.

Se calculan explicitamente:

- `P(Y_hat=0 | Y=1)`: probabilidad de detectar 0 cuando se envio 1.
- `P(Y_hat=1 | Y=0)`: probabilidad de detectar 1 cuando se envio 0.
- `P_e`: probabilidad total de error.

## Metodologia

El flujo principal del notebook es:

1. Generar conjuntos independientes de entrenamiento, validacion y prueba.
2. Estimar probabilidades, medias y varianzas solo con el conjunto de entrenamiento.
3. Normalizar las observaciones usando estadisticas del entrenamiento.
4. Entrenar una MLP `1 -> 32 -> 32 -> 16 -> 1` con capas ocultas `tanh` y salida sigmoide.
5. Seleccionar el umbral optimo de decision con el conjunto de validacion.
6. Evaluar el detector final una sola vez sobre el conjunto de prueba.
7. Comparar contra MAP gaussiano estimado, KDE y Bayes optimo.
8. Analizar robustez ante ruido y distribuciones multimodales.
9. Evaluar generalizacion ante niveles de ruido desconocidos sin reentrenamiento.

## Requisitos

Se recomienda usar Python 3.9 o superior con Jupyter instalado.

Dependencias principales:

```bash
pip install numpy matplotlib jupyter
```

El notebook no depende de frameworks de deep learning como TensorFlow o PyTorch. La red neuronal, el entrenamiento con Adam, el calculo de metricas y los detectores de referencia se implementan directamente en NumPy.

## Ejecucion

Desde esta carpeta:

```bash
jupyter notebook "Detector_binario_MLP (1).ipynb"
```

Luego ejecutar las celdas en orden. Algunas secciones de benchmark repiten entrenamientos con varias semillas, por lo que pueden tardar mas que la primera evaluacion del detector.

## Resultados principales

El notebook muestra que:

- En escenarios gaussianos correctamente modelados, MAP, KDE, Bayes y MLP obtienen errores muy cercanos.
- En distribuciones multimodales, el MAP gaussiano falla porque resume cada clase con una sola media y varianza.
- La MLP aprende fronteras no lineales y se acerca al rendimiento de Bayes cuando la estructura de las clases es mas compleja.
- KDE tambien se aproxima a Bayes en una dimension, pero puede volverse costoso en dimensiones mayores.
- Las metricas finales se calculan sobre conjuntos de prueba independientes, no sobre entrenamiento ni validacion.

Ejemplos de resultados verificados reportados en el notebook:

| Escenario | Detector | `P_e` medio |
|---|---:|---:|
| Gaussiano, sigma_1 = 2.00 | Bayes optimo | 0.22097 |
| Gaussiano, sigma_1 = 2.00 | MAP gaussiano | 0.22081 |
| Gaussiano, sigma_1 = 2.00 | MLP | 0.22113 |
| Multimodal, sigma = 0.50 | MAP gaussiano | 0.48889 |
| Multimodal, sigma = 0.50 | MLP | 0.17229 |
| Multimodal, sigma = 0.90 | MAP gaussiano | 0.44022 |
| Multimodal, sigma = 0.90 | MLP | 0.35043 |

En la prueba de generalizacion sin reentrenamiento, una MLP entrenada con `sigma=0.50` conserva una regla de decision adecuada para distintos niveles de ruido multimodal:

| sigma de prueba | MLP fija, `P_e` | MLP mezcla, `P_e` |
|---:|---:|---:|
| 0.15 | 0.00064 | 0.00072 |
| 0.30 | 0.04797 | 0.04881 |
| 0.50 | 0.17388 | 0.17449 |
| 0.70 | 0.27305 | 0.27337 |
| 0.90 | 0.34884 | 0.34999 |

## Estructura del proyecto

```text
Detector Binario MLP/
|-- Detector_binario_MLP (1).ipynb
`-- README.md
```

## Conclusiones

La MLP cumple el objetivo de actuar como detector binario entrenado a partir de datos. Cuando el modelo gaussiano es correcto, su desempeno se aproxima al MAP. Cuando las distribuciones reales son multimodales o no quedan bien representadas por media y varianza, la MLP aprende fronteras mas flexibles y reduce significativamente la probabilidad de error.
