# Optional hosted-cache production input

The selected compact example uses `cacheProduction.mode: preceding-registry`.
It does not consume an unpopulated hosted cache. To select that second tier,
replace and merge [settings.yaml](settings.yaml) into your LocalRegistry input,
then follow the [complete inventory, producer and activation procedure](../../docs/architectures/HCP_CACHE_LIFECYCLE.md).

The template deliberately contains unresolved values. It is not a release BOM,
a receipt, a live allocation or an expansion of the one-image operator trial.
Supply the reviewed inventory for your actual release/architecture/product and
add-on selection. No historical lab image counts or private helper are needed.
